/* The page is the graph. The chat is how you move through it.
 *
 * One rule holds this together: a message either names a place — and the answer is the
 * graph moving to that node and opening its card — or it is a question, and the answer
 * is the agent's steps appearing as they happen. There is no third path where the system
 * produces a paragraph you cannot check.
 */
(function () {
  "use strict";

  var state = {
    graph: null,
    node: null,
    runId: null,
    stream: null,
    events: [],
    logRun: null,
    logStream: null
  };

  var nf = new Intl.NumberFormat("es-CL");
  var clp = new Intl.NumberFormat("es-CL", { style: "currency", currency: "CLP", maximumFractionDigits: 0 });

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "class") { node.className = attrs[key]; }
      else if (key === "text") { node.textContent = attrs[key]; }
      else if (key.indexOf("on") === 0) { node.addEventListener(key.slice(2), attrs[key]); }
      else { node.setAttribute(key, attrs[key]); }
    });
    (children || []).forEach(function (child) { if (child) { node.appendChild(child); } });
    return node;
  }

  function api(path, options) {
    return fetch(path, options).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) { throw new Error(body.error || ("HTTP " + response.status)); }
        return body;
      });
    });
  }

  function count(value) {
    var number = Number(value);
    return isFinite(number) ? nf.format(number) : "—";
  }

  function shortMonto(value) {
    var number = Number(value);
    if (!isFinite(number) || number <= 0) { return "—"; }
    if (number >= 1e9) { return "$" + (number / 1e9).toFixed(1).replace(".", ",") + " mil M"; }
    if (number >= 1e6) { return "$" + Math.round(number / 1e6) + " M"; }
    return clp.format(number);
  }

  // ------------------------------------------------------------------ graph
  state.graph = new window.GraphView($("mapa"), {
    legend: true,
    onSelect: function (node) {
      // With one node pinned, the next click is the other end of the question, not a new card.
      // Otherwise the gesture would be "shift-click, remember the instructions, shift-click".
      if (state.graph.pinned.length === 1) {
        var primero = state.graph.pinned[0];
        if (primero !== node.id) {
          state.graph.pin(node.id);
          buscarCamino(primero, node.id);
          return;
        }
      }
      openCard(node.id, node.name, node.label);
    },
    onPin: function (node, pinned) { onPin(node, pinned); },
    onExpand: function (node) { expandir(node); }
  });

  //: The instance, reachable from the console. Not a debugging leftover: the demo is recorded by a
  //: script that has to fly the camera and pin two nodes at exact moments, and driving the same
  //: object the page uses is the only way to record what the product actually does.
  window.expedienteGrafo = state.graph;

  /* Dos cámaras, un solo grafo.
   *
   * El mapa contesta "dónde está esto respecto de aquello". La cámara 3D contesta "qué forma
   * tiene", y para eso hay que poder girarlo: en un plano, toda relación vecina se cruza con
   * otra y la estructura desaparece.
   *
   * La foto que alimenta el 3D es la del mapa (`snapshot`), no la respuesta del servidor: si el
   * foco dejó ochocientos nodos afuera, acá tampoco aparecen.
   */
  function vista3d() {
    if (!state.graph3d) {
      state.graph3d = new window.Graph3D($("mapa-3d"), {
        onSelect: function (node) { openCard(node.id, node.name, node.label); },
        onExpand: function (node) { expandir(node); }
      });
    }
    return state.graph3d;
  }

  //: Todo lo que cambie el mapa cambia la cámara: una sola foto, dos dibujos.
  var _setData = state.graph.setData.bind(state.graph);
  state.graph.setData = function (data, options) {
    _setData(data, options);
    if (state.graph3d) { state.graph3d.setData(state.graph.snapshot()); }
  };

  function enTresD() { return document.body.classList.contains("en-3d"); }

  function paintVista() {
    var button = $("btn-vista");
    if (!button) { return; }
    var activo = enTresD();
    button.textContent = activo ? "▦" : "◈";
    button.title = activo ? "Volver al mapa 2D" : "Girar el grafo en 3D";
    button.setAttribute("aria-pressed", String(activo));
  }

  if ($("btn-vista")) {
    $("btn-vista").addEventListener("click", function () {
      if (enTresD()) {
        if (state.graph3d) { state.graph3d.hide(); }
        document.body.classList.remove("en-3d");
      } else {
        var camara = vista3d();
        camara.setData(state.graph.snapshot());
        document.body.classList.add("en-3d");
        camara.show();
      }
      paintVista();
    });
    paintVista();
  }

  //: Al cambiar el tamaño de la ventana, la cámara 3D tiene que enterarse: si no, el volumen
  //: queda recortado por un lienzo del porte anterior.
  window.addEventListener("resize", function () {
    if (state.graph3d && enTresD()) { state.graph3d.resize(); }
  });

  document.querySelectorAll("[data-mapa]").forEach(function (button) {
    button.addEventListener("click", function () {
      var action = button.dataset.mapa;
      if (action === "in") { state.graph.zoomBy(1.25); }
      if (action === "out") { state.graph.zoomBy(0.8); }
      if (action === "reset") { state.graph.reset(); closeCard(); if ($("camino")) { $("camino").hidden = true; } }
    });
  });

  function boot() {
    api("/api/summary").then(function (data) {
      var nodes = (data.stats && data.stats.nodes) || {};
      $("n-ordenes").textContent = count(nodes.OrdenCompra);
      $("n-proveedores").textContent = count(nodes.Proveedor);
      $("n-organismos").textContent = count(nodes.Organismo);
      $("n-documentos").textContent = count(nodes.Documento);
    }).catch(function () { /* the counters are decoration; the graph is not */ });

    api("/api/overview").then(function (data) {
      state.graph.setData(data);
      renderLayers();
      $("letra-chica").textContent = data.note || "";
      say("agente", "Escribe el nombre de una municipalidad, un servicio público o una empresa. "
        + "Si prefieres, hazme una pregunta y la investigo paso a paso.");
    }).catch(function (error) {
      say("agente", "No pude cargar el grafo: " + error.message);
    });
  }

  // ------------------------------------------------------------------- chat
  function say(who, text) {
    var box = el("div", { class: "mensaje " + (who === "yo" ? "yo" : "agente") }, [el("div", { text: text })]);
    $("transcripcion").appendChild(box);
    box.scrollIntoView({ block: "nearest" });
    return box;
  }

  function step(box, label, detail, kind) {
    box.appendChild(el("div", { class: "paso " + (kind || "") }, [
      el("b", { text: label }),
      el("span", { text: detail })
    ]));
    box.scrollIntoView({ block: "nearest" });
  }

  $("form-chat").addEventListener("submit", function (event) {
    event.preventDefault();
    var input = $("pregunta");
    var text = input.value.trim();
    if (!text) { return; }
    input.value = "";
    input.style.height = "auto";
    ask(text);
  });

  $("pregunta").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("form-chat").requestSubmit();
    }
  });

  $("pregunta").addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(128, this.scrollHeight) + "px";
  });

  document.querySelectorAll("#sugerencias .chip").forEach(function (chip) {
    chip.addEventListener("click", function () { ask(chip.textContent.trim()); });
  });

  function ask(text) {
    say("yo", text);
    $("btn-enviar").disabled = true;
    var thinking = say("agente", "Buscando en el grafo…");

    api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    }).then(function (data) {
      thinking.remove();
      $("btn-enviar").disabled = false;

      if (data.kind === "focus") {
        var box = say("agente", data.reply);
        (data.matches || []).slice(1, 4).forEach(function (match) {
          box.appendChild(el("button", {
            class: "chip suave",
            text: match.name || match.node,
            onclick: function () { focusOn(match.node, match.name, match.label); }
          }));
        });
        if (data.can_investigate) {
          box.appendChild(el("button", {
            class: "chip",
            text: "Investigar esto con el agente",
            onclick: function () { investigate(text); }
          }));
        }
        focusOn(data.node, data.name, data.label);
        return;
      }

      if (data.kind === "path") {
        // The chat answered with a question the graph can settle: two ends and a walk between them.
        say("agente", data.reply);
        buscarCamino(data.desde, data.hasta);
        return;
      }

      if (data.kind === "run") {
        say("agente", data.reply);
        followRun(data.run_id);
        return;
      }

      say("agente", data.reply);
    }).catch(function (error) {
      thinking.remove();
      $("btn-enviar").disabled = false;
      say("agente", "No pude responder: " + error.message);
    });
  }

  function focusOn(nodeId, name, label) {
    var found = state.graph.flyTo(nodeId, 2.1);
    if (!found) {
      // the node is not in the current view: draw its neighbourhood and then fly
      api("/api/subgraph?nodo=" + encodeURIComponent(nodeId) + "&saltos=1")
        .then(function (data) {
          if (data.error) { say("agente", data.error); return; }
          state.graph.setData(data, { merge: true });
          renderLayers();
          state.graph.flyTo(nodeId, 2.1);
        });
    }
    openCard(nodeId, name, label);
    if (state.graph3d && document.body.classList.contains("en-3d")) {
      state.graph3d.flyTo(nodeId);
    }
  }



  /* Walk outwards from a node: two hops, merged into what is already on screen.
   *
   * This is the gesture that makes the two-company case reachable at all. Searching "Cardiotec"
   * brings one supplier and the address it is registered at; the second company with the same
   * address is two hops away and never arrives on its own. Without a way to walk, the graph is an
   * illustration of a search result instead of a thing you explore.
   */
  function expandir(node) {
    say("agente", "Abriendo el vecindario de " + (node.name || node.id) + "…");
    api("/api/subgraph?nodo=" + encodeURIComponent(node.id) + "&saltos=2")
      .then(function (data) {
        if (data.error) { say("agente", data.error); return; }
        var antes = state.graph.nodes.length;
        state.graph.setData(data, { merge: true });
        renderLayers();
        var nuevos = state.graph.nodes.length - antes;
        say("agente", nuevos > 0
          ? "Aparecieron " + nuevos + " nodos alrededor de " + (node.name || node.id) + "."
          : "No hay nada nuevo alrededor de " + (node.name || node.id) + ".");
        if (state.graph.mode === "3d") { state.graph.frameNodes(data.nodes.map(function (n) { return n.id; }), 1.15); }
        else { state.graph.flyTo(node.id, 1.9); }
      })
      .catch(function (error) { say("agente", "No pude abrir el vecindario: " + error.message); });
  }

  // ------------------------------------------------------------------ camino
  /* "¿Cómo se conectan?" — dos nodos, un clic con shift, y el grafo contesta con la ruta.
   *
   * The answer is a list of hops, each with its relation and its direction, because "two hops"
   * proves nothing while "TIENE_DOMICILIO, walked backwards, through this address" is the finding.
   * When there is no path the panel says how many hops it looked at — an empty list would read as
   * "these two are unrelated", which is a different and usually false statement.
   */
  function onPin(node, pinned) {
    say("agente", "Fijado: " + (node.name || node.id) + (pinned.length === 2 ? " · buscando el camino…" : " · fija un segundo nodo para ver cómo se conectan"));
    if (pinned.length < 2) {
      $("camino").hidden = true;
      return;
    }
    buscarCamino(pinned[0], pinned[1]);
  }

  function buscarCamino(desde, hasta) {
    var panel = $("camino");
    var cuerpo = $("camino-cuerpo");
    cuerpo.innerHTML = "";
    cuerpo.appendChild(el("p", { class: "nota", text: "Buscando hasta 4 saltos…" }));
    panel.hidden = false;
    if ($("capas")) { $("capas").hidden = true; }      // no tapar la evidencia con el menú
    if ($("registro")) { $("registro").hidden = true; } // la derecha muestra una cosa a la vez

    api("/api/path?desde=" + encodeURIComponent(desde) + "&hasta=" + encodeURIComponent(hasta))
      .then(function (data) {
        cuerpo.innerHTML = "";
        if (data.error) {
          cuerpo.appendChild(el("p", { class: "nota fuerte", text: data.error }));
          return;
        }
        if (!data.caminos || !data.caminos.length) {
          cuerpo.appendChild(el("p", {
            class: "nota fuerte",
            text: data.motivo || "no encontré un camino entre estos dos nodos"
          }));
          cuerpo.appendChild(el("p", {
            class: "nota",
            text: "No es lo mismo que «no están relacionados»: es que no aparece una ruta en "
              + (data.saltos || 4) + " saltos."
          }));
          return;
        }

        var camino = data.caminos[0];

        // The verdict, in words a person can read — and with its limit attached, because the whole
        // product is that a relation in the registry is not an imputation.
        var rels = camino.tramos.map(function (t) { return t.rel; });
        var compartido = rels.some(function (r) { return r.indexOf("COMPARTE") === 0; })
          || rels.filter(function (r) { return r.indexOf("DOMICILIO") >= 0; }).length >= 2;
        cuerpo.appendChild(el("p", {
          class: "nota fuerte",
          style: "color:#7dd3fc;font-weight:600;font-size:13px;line-height:1.45",
          text: compartido
            ? "Dos entidades distintas con un domicilio en común. Es una relación en el registro público, "
              + "no una imputación: lo que sigue es revisar quién firma y qué se compró."
            : "Estas dos entidades están conectadas por " + rels.length + " relación(es) documentadas."
        }));

        // A citation is an id; a sentence is a name. The reader is looking at companies, not at
        // `Proveedor/76.264.138-0`, so the panel speaks in names and keeps the id as the tooltip.
        var nombres = {};
        (data.nodos || []).forEach(function (nodo) { nombres[nodo.id] = nodo.name || nodo.id; });
        cuerpo.appendChild(el("p", {
          class: "nota",
          text: camino.saltos + (camino.saltos === 1 ? " salto" : " saltos") + " entre "
            + (nombres[data.desde] || data.desde) + " y " + (nombres[data.hasta] || data.hasta)
            + (data.truncado ? " · búsqueda acotada (" + data.truncado + ")" : "")
        }));

        camino.tramos.forEach(function (tramo) {
          cuerpo.appendChild(el("div", { class: "tramo" }, [
            el("b", { text: nombres[tramo.de] || tramo.de, title: tramo.de }),
            el("span", {
              class: "rel" + (tramo.reversa ? " reversa" : ""),
              text: tramo.rel + (tramo.reversa ? " ↩" : ""),
              title: tramo.reversa ? "recorrida en sentido inverso al que está guardado" : ""
            }),
            el("b", { text: nombres[tramo.a] || tramo.a, title: tramo.a })
          ]));
        });

        // The route is drawn on the canvas, one hop after another, so the answer is the path.
        var ids = [data.desde];
        camino.tramos.forEach(function (tramo) { ids.push(tramo.a); });
        state.graph.setData({ nodes: data.nodos, edges: data.aristas, seed: data.desde }, { merge: true });
        state.graph.showPath(ids);
        // Always frame: a route drawn off screen is a claim with no picture next to it.
        state.graph.frameNodes(ids, 1.25);
      })
      .catch(function (error) {
        cuerpo.innerHTML = "";
        cuerpo.appendChild(el("p", { class: "nota fuerte", text: "No pude buscar el camino: " + error.message }));
      });
  }

  if ($("cerrar-camino")) {
    $("cerrar-camino").addEventListener("click", function () {
      $("camino").hidden = true;
      state.graph.clearPins();
    });
  }

  // ------------------------------------------------------------------ card
  function closeCard() { $("tarjeta").hidden = true; }
  $("cerrar-tarjeta").addEventListener("click", closeCard);

  function openCard(nodeId, name, label) {
    state.node = nodeId;
    var body = $("tarjeta-cuerpo");
    body.innerHTML = "";
    body.appendChild(el("span", { class: "etiqueta", text: label || nodeId.split("/")[0] }));
    body.appendChild(el("h2", { text: name || nodeId }));
    var box = el("div", { id: "perfil" }, [
      el("div", { class: "cargando" }, [el("span", { class: "punto" }), el("span", { text: "Leyendo los registros…" })])
    ]);
    body.appendChild(box);
    $("tarjeta").hidden = false;
    loadProfile(nodeId);
  }

  function bar(value, max, cls) {
    var width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
    return el("span", { class: "barra " + (cls || "") }, [
      el("span", { class: "barra-fill", style: "width:" + width + "%" })
    ]);
  }

  function loadProfile(nodeId) {
    var box = $("perfil");
    if (!box) { return; }
    api("/api/profile?nodo=" + encodeURIComponent(nodeId)).then(function (data) {
      box.innerHTML = "";
      if (data.error) { box.appendChild(el("p", { class: "vacio", text: data.error })); return; }

      var kpis = el("div", { class: "kpis" });
      function kpi(value, label, hint) {
        kpis.appendChild(el("div", { class: "kpi", title: hint || "" }, [
          el("span", { class: "kpi-n", text: value }),
          el("span", { class: "kpi-l", text: label })
        ]));
      }
      kpi(count(data.orders), "órdenes");
      kpi(shortMonto(data.total_clp), "monto");
      kpi(shortMonto(data.average_clp), "promedio");
      if (data.counterparty_label) {
        kpi(count(data.counterparties_total), data.counterparty_label === "Proveedor" ? "proveedores" : "organismos");
      }
      if (typeof data.hhi === "number") { kpi(count(data.hhi), "HHI", "Sobre 2.500 se considera concentrado"); }
      box.appendChild(kpis);

      if (data.claim) {
        box.appendChild(el("h3", { text: "La afirmación" }));
        box.appendChild(el("p", { text: data.claim }));
      }

      if (data.flags && data.flags.length) {
        var flags = el("div", { class: "banderas" });
        data.flags.forEach(function (flag) {
          flags.appendChild(el("div", { class: "bandera " + flag.level }, [
            el("strong", { text: flag.flag }),
            el("span", { text: flag.detail })
          ]));
        });
        box.appendChild(el("h3", { text: "Qué mirar" }));
        box.appendChild(flags);
      }

      if (data.by_year && data.by_year.length) {
        box.appendChild(el("h3", { text: "Por año" }));
        var maxYear = Math.max.apply(null, data.by_year.map(function (y) { return y.orders; }));
        var chart = el("div", { class: "anios" });
        data.by_year.forEach(function (year) {
          chart.appendChild(el("div", { class: "anio" }, [
            el("span", { class: "anio-l", text: year.year }),
            bar(year.orders, maxYear),
            el("span", { class: "anio-v", text: count(year.orders) })
          ]));
        });
        box.appendChild(chart);
      }

      if (data.procedures && data.procedures.length) {
        box.appendChild(el("h3", { text: "Por procedimiento" }));
        var maxProc = Math.max.apply(null, data.procedures.map(function (x) { return x.ordenes; }));
        var procs = el("div", { class: "anios" });
        data.procedures.forEach(function (item) {
          procs.appendChild(el("div", { class: "anio" }, [
            el("span", { class: "anio-l ancho", text: item.procedimiento }),
            bar(item.ordenes, maxProc, item.procedimiento === "Trato Directo" ? "alerta" : ""),
            el("span", { class: "anio-v", text: count(item.ordenes) })
          ]));
        });
        box.appendChild(procs);
      }

      if (data.counterparties && data.counterparties.length) {
        box.appendChild(el("h3", { text: data.counterparty_label === "Proveedor" ? "Sus compradores" : "Sus proveedores" }));
        var maxCp = Math.max.apply(null, data.counterparties.map(function (c) { return c.orders; }));
        var list = el("div", { class: "anios" });
        data.counterparties.forEach(function (party) {
          var row = el("div", { class: "anio clickable" }, [
            el("span", { class: "anio-l ancho", text: String(party.name).slice(0, 30) }),
            bar(party.orders, maxCp),
            el("span", { class: "anio-v", text: count(party.orders) })
          ]);
          row.addEventListener("click", function () { focusOn(party.node, party.name); });
          list.appendChild(row);
        });
        box.appendChild(list);
      }

      if (data.network_peers && data.network_peers.length) {
        box.appendChild(el("h3", { text: "Mismo domicilio registrado" }));
        data.network_peers.forEach(function (peer) {
          box.appendChild(el("div", { class: "fila-item", onclick: function () {
            focusOn("Proveedor/" + peer.rut, peer.razon_social);
          } }, [
            el("span", { class: "nombre", text: peer.razon_social || peer.rut }),
            el("span", { class: "meta", text: count(peer.orders) + " órdenes · RUT " + peer.rut })
          ]));
        });
        box.appendChild(el("p", { class: "pie", text: "Un domicilio compartido también es lo que parece un edificio de oficinas: es una pista, no una relación afirmada." }));
      }

      if (data.signals && data.signals.length) {
        box.appendChild(el("h3", { text: "Señales que la mencionan" }));
        data.signals.forEach(function (signal) {
          box.appendChild(el("button", { class: "fila-item", onclick: function () {
            focusOn("Senal/" + signal.id, String(signal.claim).slice(0, 80), "Senal");
          } }, [
            el("span", { class: "nombre", text: String(signal.claim).slice(0, 110) }),
            el("span", { class: "meta", text: signal.detector + " · " + count(signal.ordenes) + " órdenes" })
          ]));
        });
      }

      // the evidence case, when the node is a signal
      if (data.case && data.case.orders) {
        box.appendChild(el("h3", { text: "Las órdenes del caso" }));
        data.case.orders.slice(0, 6).forEach(function (order) {
          box.appendChild(el("div", { class: "fila-item", onclick: function () {
            focusOn("OrdenCompra/" + order.code, order.code, "OrdenCompra");
          } }, [
            el("span", { class: "nombre", text: (order.descripcion || order.nombre || order.code).slice(0, 80) }),
            el("span", { class: "meta", text: order.code + " · " + shortMonto(order.monto_clp) + (order.procedencia ? " · " + order.procedencia : "") })
          ]));
          if (order.codigo_licitacion) {
            box.appendChild(el("button", {
              class: "chip suave",
              text: "Licitación " + order.codigo_licitacion,
              onclick: function () {
                focusOn("Licitacion/" + order.codigo_licitacion, "Licitación " + order.codigo_licitacion, "Licitacion");
              }
            }));
          }
          box.appendChild(evidenceBlock(order.code));
        });
        if (data.case.orders.length > 8) {
          box.appendChild(el("p", { class: "pie", text: "… y " + (data.case.orders.length - 8) + " más en el expediente." }));
        }
      }

      if (nodeId.indexOf("Licitacion/") === 0) {
        box.appendChild(el("h3", { text: "La licitación" }));
        var lic = el("div", { class: "verificacion" });
        var licOut = el("div", { class: "resultado-verificacion" });
        lic.appendChild(el("button", {
          class: "chip", text: "Traer la licitación desde el origen",
          onclick: function () { verify("licitacion", nodeId.split("/")[1], licOut); }
        }));
        lic.appendChild(licOut);
        box.appendChild(lic);
      }

      var actions = el("div", { class: "acciones" });
      if (nodeId.indexOf("Senal/") === 0) {
        var key = nodeId.split("/")[1];
        [["Expediente PDF", "pdf"], ["CSV", "csv"], ["JSON", "json"]].forEach(function (pair) {
          actions.appendChild(el("a", { class: "chip", href: "/api/signals/" + key + "." + pair[1], text: pair[0] }));
        });
      }
      actions.appendChild(el("button", {
        class: "chip suave", text: "Expandir un paso",
        onclick: function () { expand(nodeId); }
      }));
      actions.appendChild(el("button", {
        class: "chip suave", text: "Investigar con el agente",
        onclick: function () {
          ask("¿Qué se puede afirmar con los registros de " + (data.name || nodeId) + "? Revisa sus órdenes, sus señales y sus documentos.");
        }
      }));
      box.appendChild(actions);

      if (data.note) { box.appendChild(el("p", { class: "pie", text: data.note })); }
    }).catch(function (error) {
      box.innerHTML = "";
      box.appendChild(el("p", { class: "vacio", text: "No se pudo leer el perfil: " + error.message }));
    });
  }

  function verify(kind, code, box) {
    box.innerHTML = "";
    box.appendChild(el("div", { class: "cargando" }, [
      el("span", { class: "punto" }),
      el("span", { text: "Consultando la API de Mercado Público…" })
    ]));
    api("/api/verify/" + kind + "/" + encodeURIComponent(code)).then(function (data) {
      box.innerHTML = "";
      if (data.error) {
        box.appendChild(el("p", { class: "pie", text: data.error }));
        return;
      }
      var state_ = data.verified ? "ok" : "no";
      box.appendChild(el("div", { class: "bandera " + (data.verified ? "media" : "alta") }, [
        el("strong", { text: data.verified ? "coincide" : "revisar" }),
        el("span", { text: data.meaning || data.note || "" })
      ]));
      var rows = [
        ["huella ahora", (data.hash_now || "—").slice(0, 32)],
        ["huella registrada", (data.hash_stored || "—").slice(0, 32)],
        ["consultado", data.retrieved_at || "—"]
      ];
      var dl = el("dl");
      rows.forEach(function (row) {
        dl.appendChild(el("dt", { text: row[0] }));
        dl.appendChild(el("dd", { text: row[1] }));
      });
      box.appendChild(dl);
      if (data.record && Object.keys(data.record).length) {
        var rec = el("div", { class: "registro-origen" });
        Object.keys(data.record).slice(0, 6).forEach(function (key) {
          rec.appendChild(el("div", { class: "fila-item" }, [
            el("span", { class: "nombre", text: String(data.record[key]).slice(0, 120) }),
            el("span", { class: "meta", text: key })
          ]));
        });
        box.appendChild(rec);
      }
      if (data.source_url) {
        box.appendChild(el("a", {
          class: "chip", href: data.source_url, target: "_blank", rel: "noopener",
          text: "Abrir la fuente oficial"
        }));
      }
    }).catch(function (error) {
      box.innerHTML = "";
      box.appendChild(el("p", { class: "pie", text: "No se pudo verificar: " + error.message }));
    });
  }

  function evidenceBlock(code) {
    var wrap = el("div", { class: "verificacion" });
    var actions = el("div", { class: "acciones" });
    actions.appendChild(el("a", {
      class: "chip", target: "_blank", rel: "noopener",
      href: "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idOC=" + encodeURIComponent(code),
      text: "Original en Mercado Público"
    }));
    actions.appendChild(el("a", {
      class: "chip suave", target: "_blank", rel: "noopener",
      href: "/api/evidence/" + encodeURIComponent(code),
      text: "Lo que leímos"
    }));
    var out = el("div", { class: "resultado-verificacion" });
    actions.appendChild(el("button", {
      class: "chip suave", text: "Verificar ahora",
      onclick: function () { verify("orden", code, out); }
    }));
    wrap.appendChild(actions);
    wrap.appendChild(out);
    wrap.appendChild(el("p", {
      class: "pie",
      text: "«Original» abre la ficha oficial. «Lo que leímos» descarga el archivo exacto que se "
        + "guardó y del que sale la huella: puedes calcularle el sha256 y compararlo."
    }));
    return wrap;
  }

  function expand(nodeId) {
    api("/api/subgraph?nodo=" + encodeURIComponent(nodeId) + "&saltos=1").then(function (data) {
      if (data.error) { say("agente", data.error); return; }
      state.graph.setData(data, { merge: true });
      renderLayers();
      say("agente", "Sumé " + data.nodes.length + " nodo(s) alrededor de " + nodeId + ".");
    });
  }

  // ------------------------------------------------------------------ layers
  var NOMBRE_CAPA = {
    Detector: "Detectores",
    Senal: "Alertas",
    OrdenCompra: "Órdenes de compra",
    Documento: "Documentos",
    Proveedor: "Empresas",
    Organismo: "Organismos",
    Domicilio: "Domicilios",
    Licitacion: "Licitaciones",
    Evidencia: "Evidencia",
    Afirmacion: "Afirmaciones"
  };

  function renderLayers() {
    var counts = state.graph.counts();
    var box = $("capas");
    box.innerHTML = "";
    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).forEach(function (label) {
      var input = el("input", { type: "checkbox", id: "capa-" + label, checked: "checked" });
      input.addEventListener("change", function () { state.graph.setLabelVisible(label, input.checked); });
      box.appendChild(el("label", { class: "capa", for: "capa-" + label }, [
        input,
        el("i", { style: "background:" + state.graph.colorFor(label) }),
        document.createTextNode((NOMBRE_CAPA[label] || label) + " (" + counts[label] + ")")
      ]));
    });
  }

  //: La leyenda del lienzo y el panel de capas son dos vistas del mismo estado. El clic en una
  //: tiene que repintar la otra, o el usuario ve dos verdades distintas.
  window.expedienteActualizarCapas = renderLayers;

  $("btn-capas").addEventListener("click", function () {
    var box = $("capas");
    box.hidden = !box.hidden;
    $("btn-capas").setAttribute("aria-expanded", String(!box.hidden));
  });

  // ------------------------------------------------------------------- runs
  /* La cámara sigue la investigación.
   *
   * Cada paso del agente ya sabe qué nodos tocó (`nodosDelPaso`). Mover la cámara ahí es lo que
   * convierte el registro en navegación: no se "muestra el grafo", se viaja por él a medida que
   * la investigación avanza. Si el nodo no está en el lienzo, no se inventa un salto: se queda
   * donde está y el registro lo dice.
   */
  function seguirPaso(event) {
    if (!state.graph || !state.graph.follow) { return; }
    var ids = nodosDelPaso(event);
    if (!ids.length) { return; }
    state.graph.follow(ids, 1.1);
  }

  function followRun(runId) {
    state.runId = runId;
    // Una corrida nueva empieza en vivo: dejar la línea de tiempo en el medio de la anterior
    // mostraría el registro de una investigación que ya no es la que está en pantalla.
    detenerRebobinado();
    state.replayPos = null;
    if (state.stream) { state.stream.close(); }
    var box = el("div", { class: "mensaje agente" });
    $("transcripcion").appendChild(box);
    var source = new EventSource("/api/runs/" + runId + "/stream");
    state.stream = source;

    source.onmessage = function (message) {
      var event = JSON.parse(message.data);
      if (event.type === "stream.end") {
        source.close();
        finishRun(runId, box);
        return;
      }
      state.events.push(event);
      if (event.type === "agent.plan") {
        step(box, "plan", (event.payload.steps || []).join(" → "));
      } else if (event.type === "tool.call") {
        step(box, event.payload.tool, "", "");
        seguirPaso(event);
      } else if (event.type === "tool.result") {
        step(box, "·", event.payload.summary || "", "");
      } else if (event.type === "claim.accepted") {
        step(box, "afirmó", event.payload.texto, "ok");
        seguirPaso(event);
      } else if (event.type === "claim.rejected") {
        step(box, "descartó", event.payload.reason || "", "no");
      } else if (event.type === "budget.exceeded") {
        step(box, "límite", event.payload.limit + " " + event.payload.max, "no");
      }
    };
    source.onerror = function () { source.close(); finishRun(runId, box); };
  }

  function finishRun(runId, box) {
    api("/api/runs/" + runId).then(function (data) {
      var claims = (data.state.claims_accepted || []).length;
      var rejected = (data.state.claims_rejected || []).length;
      step(box, "listo", claims + " afirmación(es) con respaldo · " + rejected + " descartada(s)", "ok");
      box.appendChild(el("button", {
        class: "chip", text: "Ver el registro completo",
        onclick: function () { openLog(runId); }
      }));
    });
  }

  function investigate(text) { ask(text); }

  // ------------------------------------------------------------------ registro
  $("btn-registro").addEventListener("click", function () {
    // La misma columna: el registro reemplaza al camino, no se apila encima.
    if ($("camino")) { $("camino").hidden = true; } openLog(state.runId); });
  $("cerrar-registro").addEventListener("click", function () { $("registro").hidden = true; });
  $("solo-importantes").addEventListener("change", renderEvents);

  if ($("linea-tiempo")) {
    $("linea-tiempo").addEventListener("input", function () {
      detenerRebobinado();
      aplicarRebobinado(Number($("linea-tiempo").value));
      renderEvents();
    });
  }
  if ($("btn-rebobinar")) {
    $("btn-rebobinar").addEventListener("click", alternarRebobinado);
  }
  // Espacio reproduce o pausa, pero nunca mientras alguien escribe en el compositor.
  document.addEventListener("keydown", function (event) {
    if (event.code !== "Space") { return; }
    var activo = document.activeElement;
    if (activo && (activo.tagName === "TEXTAREA" || activo.tagName === "INPUT")) { return; }
    if (!state.events || !state.events.length) { return; }
    event.preventDefault();
    alternarRebobinado();
  });

  function openLog(runId) {
    $("registro").hidden = false;
    api("/api/runs?limit=30").then(function (data) {
      var select = $("selector-run");
      select.innerHTML = "";
      (data.runs || []).forEach(function (run) {
        select.appendChild(el("option", { value: run.id, text: (run.question || run.id).slice(0, 70) }));
      });
      var target = runId || (data.runs && data.runs[0] && data.runs[0].id);
      if (target) { select.value = target; loadLog(target); }
    });
  }

  $("selector-run").addEventListener("change", function (event) { loadLog(event.target.value); });

  function loadLog(runId) {
    if (state.logStream) { state.logStream.close(); }
    state.logRun = runId;
    api("/api/runs/" + runId).then(function (data) {
      state.events = data.events || [];
      renderEvents();
      var finished = (data.state || {}).finished || (data.state || {}).failed;
      if (!finished) {
        var source = new EventSource("/api/runs/" + runId + "/stream?since=" + state.events.length);
        state.logStream = source;
        source.onmessage = function (message) {
          var event = JSON.parse(message.data);
          if (event.type === "stream.end") { source.close(); return; }
          state.events.push(event);
          renderEvents();
        };
      }
    });
  }

  var ETIQUETA = {
    "run.started": "inicio", "run.finished": "cierre", "run.failed": "interrumpida",
    "agent.plan": "plan", "agent.step": "paso", "tool.call": "herramienta",
    "tool.result": "resultado", "tool.error": "error", "claim.accepted": "afirmación",
    "claim.rejected": "descarte", "source.added": "fuente", "source.rejected": "fuente rechazada",
    "budget.exceeded": "límite", "dossier.built": "expediente"
  };
  var IMPORTANTES = { "agent.step": 1, "claim.accepted": 1, "claim.rejected": 1, "source.added": 1, "budget.exceeded": 1, "dossier.built": 1, "run.finished": 1, "agent.plan": 1 };

  /* El waterfall: cada paso con su duración, su objeto y su familia de verbo.
   *
   * The old log was a list of sentences. A step has two facts that a sentence hides — how long it
   * took and what it touched — and both fit in a row: a bar whose width is the duration against the
   * slowest step of the run, and a verb whose colour is its family. The shape of a run then reads
   * before a single word is read: mostly cyan means it navigated, blue means it went to documents,
   * any red is a claim that was deleted and is the most interesting row on the screen.
   */
  var FAMILIA = {
    buscar_en_grafo: "navegar", vecinos: "navegar", caminos: "navegar",
    red_proveedor: "navegar", senales_de: "navegar", perfil_de_gasto: "navegar",
    senal_detalle: "documentar", citas: "documentar", documento_de_orden: "documentar",
    buscar_en_web: "documentar", leer_fuente: "documentar",
    afirmar: "decidir", terminar: "cerrar"
  };

  //: Which nodes a step touched. The log and the canvas are two views of one run; hovering a row
  //: has to be able to point at the records that row is about, or the waterfall is just a prettier
  //: list of strings.
  function nodosDelPaso(event) {
    // A tool.call stores its arguments under `arguments`, not at the top level: `buscar_en_grafo`
    // logs {tool, arguments:{texto}}, so reading `payload.nodo` found nothing and no row in the
    // waterfall could point at the graph. Both shapes are accepted because claim events are flat.
    var payload = (event.payload && event.payload.arguments) || event.payload || {};
    var ids = [];
    if (payload.nodo) { ids.push(String(payload.nodo)); }
    if (payload.desde) { ids.push(String(payload.desde)); }
    if (payload.hasta) { ids.push(String(payload.hasta)); }
    if (payload.codigo) { ids.push("OrdenCompra/" + payload.codigo); }
    // `perfil_de_gasto` and `red_proveedor` are called with a RUT, not a node id — the one tool that
    // takes the identifier the user actually knows. Without this the row for the most expensive
    // step in the run was the only one that could not point at anything.
    if (payload.rut) { ids.push("Proveedor/" + String(payload.rut).trim()); }
    if (payload.source_id) { ids.push(String(payload.source_id)); }
    if (Array.isArray(payload.ordenes)) {
      payload.ordenes.forEach(function (code) {
        ids.push("OrdenCompra/" + (typeof code === "string" ? code : (code && code.codigo) || ""));
      });
    }
    return ids.filter(Boolean);
  }

  function resumenDe(payload) {
    var text = payload.summary || payload.texto || payload.text || payload.error
      || payload.url || payload.reason || "";
    text = String(text);
    return text.length > 68 ? text.slice(0, 66) + "…" : text;
  }

  /* One row per step, not per event — and the model's own time is a step.
   *
   * The log emits a pair for each thing that happens: `tool.call` then `tool.result`, and
   * `model.request` then `model.response`. Only the second of each pair knows how long it took, so
   * rendering events one by one shows every step twice, once with no time and once with no verb.
   *
   * Pairing them also makes visible the distinction the whole observability tooling industry exists
   * for: **time spent thinking and time spent reading the graph are not the same time**, and the
   * tokens are only on one side. A run that is 90% model time and 10% declared graph work is an
   * expensive prompt with a graph attached, and the waterfall should say so at a glance.
   */
  function pasosDe(eventos) {
    var ts = {};
    eventos.forEach(function (event) { ts[event.seq] = Number(event.ts || 0); });
    var pasos = [];

    for (var index = 0; index < eventos.length; index += 1) {
      var event = eventos[index];
      var payload = event.payload || {};

      // A response or a result is consumed by the request or call that opened it.
      if (event.type === "tool.result" || event.type === "tool.error"
        || event.type === "model.response") { continue; }

      if (event.type === "tool.call") {
        var resultado = null;
        for (var look = index + 1; look < eventos.length; look += 1) {
          var siguiente = eventos[look];
          if (siguiente.type === "tool.result" || siguiente.type === "tool.error") {
            if ((siguiente.payload || {}).tool === payload.tool) { resultado = siguiente; }
            break;
          }
          if (siguiente.type === "tool.call") { break; }
        }
        var ms = Number((resultado && resultado.payload || {}).duration_ms || 0);
        if (!ms && resultado) { ms = Math.round((ts[resultado.seq] - ts[event.seq]) * 1000); }
        pasos.push({
          tipo: "tool", herramienta: payload.tool, payload: payload, ms: Math.max(0, ms),
          resumen: ((resultado && resultado.payload) || payload),
          fallo: !!(resultado && resultado.type === "tool.error")
        });
        continue;
      }

      if (event.type === "model.request") {
        var respuesta = null;
        for (var scan = index + 1; scan < eventos.length; scan += 1) {
          if (eventos[scan].type === "model.response") { respuesta = eventos[scan]; break; }
        }
        var uso = (respuesta && respuesta.payload && respuesta.payload.usage) || {};
        var espera = respuesta ? Math.round((ts[respuesta.seq] - ts[event.seq]) * 1000) : 0;
        pasos.push({
          tipo: "model", herramienta: payload.purpose || "modelo", payload: payload,
          ms: Math.max(0, espera), uso: uso, fallo: false,
          resumen: { summary: (uso.total_tokens ? uso.total_tokens + " tokens" : "") }
        });
        continue;
      }

      pasos.push({
        tipo: event.type, payload: payload, ms: Number(payload.duration_ms || 0),
        resumen: payload, fallo: false
      });
    }
    return pasos;
  }


  /* Rebobinar la grabación.
   *
   * `state.replayPos` is how many steps of the recording have "happened". Null means live: the
   * whole thing. Moving it does not call the server and does not re-run the agent — it re-reads
   * events already in memory and repaints the canvas with the set of records that existed at that
   * point, plus the ones whose claim had been deleted by then, in red.
   *
   * That is the same claim the dossier makes about its own replay: the artefact can be rebuilt
   * from its recording with no model, no network and no database. This is that sentence, on
   * screen, with a handle you can drag.
   */
  function aplicarRebobinado(pos) {
    var pasos = state.pasos || [];
    var hasta = Math.max(0, Math.min(pos == null ? pasos.length : pos, pasos.length));
    state.replayPos = hasta;

    var vivos = [];
    var rechazados = [];
    pasos.slice(0, hasta).forEach(function (paso) {
      var ids = nodosDelPaso({ type: paso.tipo, payload: paso.payload });
      ids.forEach(function (id) {
        if (vivos.indexOf(id) < 0) { vivos.push(id); }
      });
      if (paso.tipo === "claim.rejected") {
        ids.forEach(function (id) { if (rechazados.indexOf(id) < 0) { rechazados.push(id); } });
      }
    });

    if (state.graph && state.graph.setReplay) {
      state.graph.setReplay(vivos.slice(0, 400), rechazados.slice(0, 40));
    }

    pintarEstadoRebobinado(hasta, pasos.length, vivos.length, rechazados.length);
  }

  /* La etiqueta y la nota, sin tocar el grafo.
   *
   * En vivo no se llama a `setReplay`: iluminar «todos los registros tocados» durante una corrida
   * normal convertiría el estado por defecto en un resaltado permanente, y entonces el resaltado
   * dejaría de significar algo.
   */
  function pintarEstadoRebobinado(hasta, total, vivos, rechazados) {
    var linea = $("linea-tiempo");
    var nota = $("rebobinado-nota");
    if (linea && document.activeElement !== linea) { linea.value = String(hasta); }
    if ($("rebobinado-pos")) { $("rebobinado-pos").textContent = hasta + "/" + total; }
    if (!nota) { return; }
    var vivo = hasta >= total;
    nota.classList.toggle("activo", !vivo);
    nota.textContent = !total ? "sin grabación cargada"
      : vivo ? "en vivo · la grabación completa, " + total + " pasos"
        : "rebobinando · paso " + hasta + " de " + total
          + " · sin modelo, sin red, sin base de datos · " + vivos + " registros tocados"
          + (rechazados ? " · " + rechazados + " descartados" : "");
  }

  function detenerRebobinado() {
    if (state.replayTimer) { clearInterval(state.replayTimer); state.replayTimer = null; }
    if ($("btn-rebobinar")) { $("btn-rebobinar").textContent = "▶"; }
  }

  function alternarRebobinado() {
    var pasos = state.pasos || [];
    if (!pasos.length) { return; }
    if (state.replayTimer) { detenerRebobinado(); return; }
    var inicio = state.replayPos == null || state.replayPos >= pasos.length ? 0 : state.replayPos;
    if ($("btn-rebobinar")) { $("btn-rebobinar").textContent = "⏸"; }
    aplicarRebobinado(inicio);
    state.replayTimer = setInterval(function () {
      var actual = (state.replayPos || 0) + 1;
      if (actual >= pasos.length) {
        // Llegar al final es volver a estar en vivo, no quedarse pegado al último paso: si no,
        // la próxima corrida arrancaría mirando el final de la anterior.
        state.replayPos = null;
        detenerRebobinado();
        renderEvents();
        if (state.graph && state.graph.setReplay) { state.graph.setReplay([], []); }
        return;
      }
      aplicarRebobinado(actual);
      renderEvents();
    }, 620);
  }

  function renderEvents() {
    var list = $("eventos");
    var onlyImportant = $("solo-importantes").checked;
    /* Pair first, filter afterwards.
     *
     * The duration lives on `tool.result`, which the "only important" filter drops — so filtering
     * the events before pairing produced a waterfall with no bars at all, which is the one thing a
     * waterfall is for. The pairing needs the whole log; the filter is about what to *show*.
     */
    var pasos = pasosDe(state.events).filter(function (paso) {
      // "Only important" hides the model's own narration, never a tool call or a decision.
      return !onlyImportant || paso.tipo !== "agent.step";
    });
    state.pasos = pasos;
    var linea = $("linea-tiempo");
    if (linea && Number(linea.max) !== pasos.length) {
      linea.max = String(pasos.length);
      if (state.replayPos == null) { linea.value = String(pasos.length); }
    }
    var posicion = state.replayPos == null ? pasos.length : state.replayPos;
    if (state.replayPos == null) {
      pintarEstadoRebobinado(pasos.length, pasos.length, 0, 0);
    }
    var maxMs = 1;
    pasos.forEach(function (paso) { if (paso.ms > maxMs) { maxMs = paso.ms; } });

    list.innerHTML = "";

    /* Una leyenda, porque el color solo informa si alguien sabe qué informa.
     * Violet = the model thinking (and its tokens). Cyan = walking the graph. Blue = going to a
     * document. Green = closing. Red = a claim that was deleted, which is the row worth reading. */
    var leyenda = el("div", { class: "leyenda-pasos" });
    [["pensar", "modelo"], ["navegar", "grafo"], ["documentar", "documento"],
     ["decidir", "afirmación"], ["descartar", "descarte"]].forEach(function (par) {
      leyenda.appendChild(el("span", { class: "clave " + par[0] }, [
        el("i", {}), document.createTextNode(par[1])
      ]));
    });
    list.appendChild(leyenda);

    var contador = 0;
    var afirmadas = 0;
    var descartadas = 0;
    var tokens = 0;
    var msModelo = 0;
    var msHerramientas = 0;
    pasos.forEach(function (paso) {
      if (paso.tipo === "model") { msModelo += paso.ms; }
      if (paso.tipo === "tool") { msHerramientas += paso.ms; }
    });

    pasos.forEach(function (paso) {
      var familia = paso.fallo || paso.tipo === "claim.rejected" || paso.tipo === "budget.exceeded"
        ? "descartar"
        : paso.tipo === "model" ? "pensar"
          : paso.tipo === "claim.accepted" ? "decidir"
            : paso.tipo === "run.finished" ? "cerrar"
              : paso.tipo === "tool" ? (FAMILIA[paso.herramienta] || "navegar")
                : "";
      if (paso.tipo === "tool") { contador += 1; }
      if (paso.tipo === "model") { tokens += Number((paso.uso || {}).total_tokens || 0); }
      if (paso.tipo === "claim.accepted") { afirmadas += 1; }
      if (paso.tipo === "claim.rejected") { descartadas += 1; }

      var verbo = paso.tipo === "tool" ? (paso.herramienta || "herramienta")
        : paso.tipo === "model" ? (paso.herramienta === "plan" ? "planear" : "pensar")
          : paso.tipo === "claim.accepted" ? "afirmó"
          : paso.tipo === "claim.rejected" ? "descartó"
            : ETIQUETA[paso.tipo] || paso.tipo;
      var ids = nodosDelPaso({ type: paso.tipo, payload: paso.payload });
      var texto = paso.tipo === "model"
        ? (paso.uso && paso.uso.total_tokens
          ? paso.uso.total_tokens + " tokens · " + (paso.uso.prompt_tokens || 0) + " de entrada"
          : "esperando al modelo")
        : resumenDe(paso.tipo === "tool" ? paso.resumen : paso.payload);
      // "0 señal(es)" three times in a row reads like a malfunction. An empty answer is a fact
      // about the record, not a failure of the run, and the row has to say which one it is.
      var vacio = paso.tipo === "tool" && /^0\b/.test(String(texto).trim());
      if (vacio) { texto = "sin resultados · " + texto; }

      var indice = pasos.indexOf(paso);
      var fila = el("li", {
        class: "paso " + familia + (ids.length ? " con-nodos" : "") + (vacio ? " vacio" : "")
          + (indice >= posicion ? " futuro" : "") + (indice === posicion - 1 ? " actual" : ""),
        style: "color:" + (familia === "pensar" ? "#c084fc"
          : familia === "navegar" ? "#22d3ee"
          : familia === "documentar" ? "#60a5fa"
            : familia === "decidir" ? "#a78bfa"
              : familia === "descartar" ? "#f87171"
                : familia === "cerrar" ? "#34d399" : "#64748b")
      }, [
        el("span", { class: "punto" }),
        el("div", {}, [
          el("div", { class: "cabecera" }, [
            el("span", { class: "verbo", text: verbo }),
            el("span", { class: "objetivo", text: texto, title: texto }),
            paso.ms ? el("span", { class: "ms", text: paso.ms + " ms" }) : null
          ]),
          paso.ms ? el("div", { class: "barra" }, [
            el("i", { style: "width:" + Math.max(3, Math.round(paso.ms / maxMs * 100)) + "%" })
          ]) : null
        ])
      ]);

      if (ids.length) {
        // The panel rebuilds itself on every event of a live run, so the row under the pointer is
        // destroyed and recreated several times a second — which fires `mouseleave` and cleared the
        // highlight the user was holding. The pointed step lives in the state and is re-applied.
        fila.addEventListener("mouseenter", function () {
          state.hoverPaso = ids;
          state.graph.setPeek(ids);
        });
        fila.addEventListener("mouseleave", function () {
          if (state.hoverPaso === ids) { state.hoverPaso = null; }
          state.graph.setPeek([]);
        });
        fila.addEventListener("click", function () {
          if (state.graph.selected !== ids[0]) { focusOn(ids[0], ids[0], ids[0].split("/")[0]); }
        });
      }
      list.appendChild(fila);
    });

    /* The closing line is the argument.
     *
     * "7 pasos" says nothing. "7 pasos · 12.480 tokens · 78% del tiempo en el modelo" says where
     * the cost is, and it is a number this project can defend because it comes from its own
     * recording — the same position as the prompt-cache figure in the README.
     */
    var total = msModelo + msHerramientas;
    var resumen = el("div", { class: "resumen-pasos" });
    resumen.textContent = contador + " pasos · " + afirmadas + " afirma."
      + (descartadas ? " · " + descartadas + " descartes" : "")
      + (tokens ? " · " + tokens.toLocaleString("es-CL") + " tok" : "")
      + (total ? " · " + (total < 1000 ? total + " ms" : (total / 1000).toFixed(1) + " s")
        + (msModelo ? " (" + Math.round(msModelo / total * 100) + "% modelo)" : "") : "");
    list.appendChild(resumen);
    if (state.hoverPaso) { state.graph.setPeek(state.hoverPaso); }
    // La corrida crece hacia abajo; el panel se queda en el final, que es donde está el desenlace.
    if (!state.hoverPaso && state.replayPos == null) { list.scrollTop = list.scrollHeight; }
  }



  boot();
})();
