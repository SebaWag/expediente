/* The portal.
 *
 * Plain browser JavaScript, no framework, no build step — the same reason the rest of
 * the project has no dependencies. Everything the reader sees comes from an API call
 * that returns either a fact with its source or a reason for refusing.
 */
(function () {
  "use strict";

  var state = {
    view: "inicio",
    runId: null,
    stream: null,
    events: [],
    logRun: null,
    logStream: null,
    graph: null
  };

  var nf = new Intl.NumberFormat("es-CL");
  var clp = new Intl.NumberFormat("es-CL", { style: "currency", currency: "CLP", maximumFractionDigits: 0 });

  // ------------------------------------------------------------------ helpers
  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "class") { node.className = attrs[key]; }
      else if (key === "text") { node.textContent = attrs[key]; }
      else if (key === "html") { node.innerHTML = attrs[key]; }
      else if (key.indexOf("on") === 0) { node.addEventListener(key.slice(2), attrs[key]); }
      else { node.setAttribute(key, attrs[key]); }
    });
    (children || []).forEach(function (child) {
      if (child) { node.appendChild(child); }
    });
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

  function hhmmss(ts) {
    if (!ts) { return "—"; }
    var date = new Date(ts * 1000);
    return date.toLocaleTimeString("es-CL", { hour12: false });
  }

  function money(value) {
    var number = Number(value);
    return isFinite(number) && number > 0 ? clp.format(number) : "—";
  }

  function count(value) {
    var number = Number(value);
    return isFinite(number) ? nf.format(number) : "—";
  }

  function shortMonto(value) {
    var number = Number(value);
    if (!isFinite(number) || number <= 0) { return ""; }
    if (number >= 1e9) { return "$" + (number / 1e9).toFixed(1).replace(".", ",") + " mil M"; }
    if (number >= 1e6) { return "$" + Math.round(number / 1e6) + " M"; }
    return clp.format(number);
  }

  // -------------------------------------------------------------------- router
  function show(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach(function (section) {
      section.hidden = section.id !== "view-" + view;
    });
    document.querySelectorAll(".tab").forEach(function (tab) {
      if (tab.dataset.view === view) { tab.setAttribute("aria-current", "page"); }
      else { tab.removeAttribute("aria-current"); }
    });
    if (view === "inicio") { loadSummary(); }
    if (view === "senales") { loadKanban(); }
    if (view === "registro") { loadRunList(); }
    if (view === "grafo") { loadRoots(); }
  }

  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () { show(tab.dataset.view); });
  });
  document.querySelectorAll("[data-goto]").forEach(function (button) {
    button.addEventListener("click", function () { show(button.dataset.goto); });
  });

  // -------------------------------------------------------------------- inicio
  function loadSummary() {
    api("/api/summary").then(function (data) {
      var nodes = (data.stats && data.stats.nodes) || {};
      $("n-ordenes").textContent = count(nodes.OrdenCompra);
      $("n-proveedores").textContent = count(nodes.Proveedor);
      $("n-organismos").textContent = count(nodes.Organismo);
      $("n-senales").textContent = count(nodes.Senal);

      var list = $("runs-recientes");
      list.innerHTML = "";
      if (!data.runs || !data.runs.length) {
        list.appendChild(el("li", { text: "Todavía no hay investigaciones. La primera puedes hacerla tú." }));
      }
      (data.runs || []).forEach(function (run) {
        var badgeClass = run.status === "finished" ? "ok" : run.status === "failed" ? "alta" : "";
        list.appendChild(el("li", {}, [
          el("span", { class: "q", text: run.question || run.id }),
          el("span", { class: "badge " + badgeClass, text: run.status === "finished" ? "cerrada" : run.status }),
          el("span", { class: "pie", text: run.claims_accepted + " afirmación(es) · " + run.sources + " fuente(s) · " + run.steps + " paso(s)" }),
          el("button", {
            class: "link",
            text: "Ver el registro →",
            onclick: function () { openLog(run.id); }
          })
        ]));
      });
    }).catch(function (error) {
      $("numeros").appendChild(el("p", { class: "descarte", text: "No se pudieron leer los totales: " + error.message }));
    });
  }

  // ------------------------------------------------------------------- kanban
  var DETECTOR_LABEL = {
    fraccionamiento: "Compras partidas",
    concentracion: "Concentración de gasto",
    colicotizacion: "Cotizan juntas",
    modificaciones: "Cambios posteriores"
  };

  var COLUMN_DEF = {
    sin_revisar: { title: "Sin revisar", def: "Nadie las ha abierto todavía." },
    en_analisis: { title: "En análisis", def: "Una investigación está corriendo ahora." },
    con_evidencia: { title: "Con evidencia", def: "Una afirmación se sostuvo con registros." },
    descartada: { title: "Descartadas", def: "Se revisaron y no alcanzó la evidencia." }
  };

  function loadKanban() {
    var detector = $("filtro-detector").value;
    api("/api/kanban?limit=120").then(function (data) {
      var board = $("kanban");
      board.innerHTML = "";
      data.columns.forEach(function (column) {
        var cards = column.cards.filter(function (card) {
          return !detector || card.detector === detector;
        });
        var columnNode = el("div", { class: "col", role: "listitem" }, [
          el("header", {}, [
            el("h3", { text: COLUMN_DEF[column.state].title }),
            el("span", { class: "cuenta", text: cards.length + " de " + column.count })
          ]),
          el("p", { class: "def", text: COLUMN_DEF[column.state].def })
        ]);
        if (!cards.length) {
          columnNode.appendChild(el("p", { class: "vacio", text: "Nada aquí por ahora." }));
        }
        cards.slice(0, 25).forEach(function (card) {
          columnNode.appendChild(renderCard(card));
        });
        board.appendChild(columnNode);
      });
    }).catch(function (error) {
      $("kanban").innerHTML = "";
      $("kanban").appendChild(el("p", { class: "descarte", text: "No se pudieron cargar las señales: " + error.message }));
    });
  }

  function renderCard(card) {
    var severity = (card.severity || "").toLowerCase();
    var who = el("span", { class: "quien" }, [
      document.createTextNode(card.proveedor || "Proveedor sin nombre"),
      el("span", { text: "→ " + (card.organismo || "organismo sin nombre") })
    ]);
    var figures = el("div", { class: "cifras" }, [
      el("span", { text: DETECTOR_LABEL[card.detector] || card.detector || "patrón" }),
      el("span", { text: count(card.ordenes) + " órdenes" }),
      el("span", { text: shortMonto(card.monto_clp) })
    ]);
    if (severity) {
      figures.appendChild(el("span", {
        class: "badge " + (severity === "alta" ? "alta" : "media"),
        text: severity
      }));
    }
    return el("button", {
      class: "card",
      "data-sev": severity,
      role: "listitem",
      onclick: function () { openSignal(card); }
    }, [who, figures]);
  }

  $("filtro-detector").addEventListener("change", loadKanban);

  function openSignal(card) {
    var panel = $("detalle-senal");
    var body = $("detalle-senal-cuerpo");
    body.innerHTML = "";
    body.appendChild(el("h2", { text: DETECTOR_LABEL[card.detector] || card.detector || "Señal" }));
    body.appendChild(el("p", {
      class: "lead small",
      text: "Esto lo encontró una regla escrita, no un modelo. Significa que el patrón está en los "
        + "registros; no significa que sea ilegal. Abajo está el detalle que se puede revisar."
    }));

    var rows = [
      ["Empresa", card.proveedor || "—"],
      ["Organismo", card.organismo || "—"],
      ["Órdenes involucradas", count(card.ordenes) + " órdenes"],
      ["Monto total", money(card.monto_clp)],
      ["Severidad", card.severity || "—"],
      ["Clase", card.clase || "—"],
      ["Período", card.periodo || "—"]
    ];
    var dl = el("dl");
    rows.forEach(function (row) {
      dl.appendChild(el("dt", { text: row[0] }));
      dl.appendChild(el("dd", { text: row[1] }));
    });
    body.appendChild(dl);

    body.appendChild(el("h3", { text: "Descargar el expediente" }));
    body.appendChild(el("p", {
      class: "pie",
      text: "El PDF trae las órdenes, la dirección de cada registro, su huella digital y el bloque de firma."
    }));
    var downloads = el("div", { class: "acciones" });
    [["PDF firmable", "pdf"], ["Markdown", "md"], ["CSV de órdenes", "csv"], ["JSON con hashes", "json"]]
      .forEach(function (pair) {
        downloads.appendChild(el("a", {
          class: "chip descarga",
          href: "/api/signals/" + card.id + "." + pair[1],
          text: pair[0]
        }));
      });
    body.appendChild(downloads);

    body.appendChild(el("h3", { text: "Qué más puedes hacer" }));
    var actions = el("div", { class: "acciones" });
    actions.appendChild(el("button", {
      class: "chip",
      text: "Ver la cadena completa en el mapa",
      onclick: function () { showNode("Senal/" + card.id); }
    }));
    actions.appendChild(el("button", {
      class: "chip",
      text: "Ver el registro de la señal",
      onclick: function () { showNode("Senal/" + card.id); }
    }));
    actions.appendChild(el("button", {
      class: "chip",
      text: "Investigar esta señal con el agente",
      onclick: function () {
        var question = "Revisa la señal " + card.id + " (" + (DETECTOR_LABEL[card.detector] || card.detector)
          + ") de " + (card.proveedor || "el proveedor") + " con " + (card.organismo || "el organismo")
          + ". ¿Qué registros la sostienen y qué se puede afirmar con esos registros?";
        $("pregunta").value = question;
        closePanels();
        show("investigar");
        $("pregunta").focus();
      }
    }));
    body.appendChild(actions);

    body.appendChild(el("h3", { text: "Sobre la red de proveedores" }));
    body.appendChild(el("p", {
      class: "pie",
      text: "Si esta señal involucra un domicilio compartido, el detalle de la red está en el mapa: "
        + "un domicilio compartido también es lo que parece un edificio de oficinas, así que es una "
        + "pista para revisar y no una relación afirmada."
    }));

    panel.hidden = false;
  }

  $("cerrar-detalle").addEventListener("click", function () { $("detalle-senal").hidden = true; });
  $("cerrar-nodo").addEventListener("click", closePanels);

  function closePanels() {
    $("detalle-senal").hidden = true;
    $("nodo-panel").hidden = true;
  }

  // --------------------------------------------------------------------- mapa
  //
  // The explorer. A search box alone is not an explorer: you cannot look at a graph
  // you have to guess a name to see. So there are starting points, a node can be
  // expanded or isolated, and the layers can be switched off when the drawing gets
  // busy. Everything here reads; nothing here writes.

  if (!state.graph && $("mapa")) {
    state.graph = new window.GraphView($("mapa"), {
      onSelect: function (node) { openNode(node); }
    });
  }

  function loadRoots() {
    var box = $("puntos-partida");
    box.innerHTML = "";
    box.appendChild(el("span", { class: "pie", text: "Cargando…" }));
    api("/api/roots").then(function (data) {
      box.innerHTML = "";
      (data.detectors || []).forEach(function (root) {
        box.appendChild(el("button", {
          class: "chip partida-chip",
          title: root.hint || "",
          text: "Patrón: " + root.label,
          onclick: function () { drawNode(root.id, 1, false); }
        }));
      });
      (data.signals || []).slice(0, 4).forEach(function (root) {
        box.appendChild(el("button", {
          class: "chip partida-chip" + (root.severity === "alta" ? " alta" : ""),
          title: root.hint || "",
          text: "Señal: " + root.label,
          onclick: function () { drawNode(root.id, 2, false); }
        }));
      });
      if (data.counts && data.counts.Proveedor) {
        box.appendChild(el("span", {
          class: "pie",
          text: "En total: " + count(data.counts.Proveedor) + " empresas · "
            + count(data.counts.Organismo) + " organismos · " + count(data.counts.OrdenCompra) + " órdenes."
        }));
      }
    }).catch(function (error) {
      box.innerHTML = "";
      box.appendChild(el("span", { class: "pie", text: "No se pudieron cargar los puntos de partida: " + error.message }));
    });
  }

  function drawNode(nodeId, depth, merge) {
    $("mapa-pie").textContent = "Leyendo el grafo alrededor de " + nodeId + "…";
    api("/api/subgraph?nodo=" + encodeURIComponent(nodeId) + "&saltos=" + (depth || 1))
      .then(function (data) {
        if (data.error) {
          $("mapa-pie").textContent = data.error;
          return;
        }
        state.graph.setData(data, { merge: !!merge });
        renderFilters();
        var total = state.graph.nodes.length;
        var shown = total;
        $("mapa-pie").textContent = (merge ? "Sumé " : "")
          + (data.nodes || []).length + " nodo(s) y " + (data.edges || []).length + " relación(es)"
          + (merge ? " · en pantalla " + total : "")
          + (data.truncated ? " · el dibujo se cortó para poder leerlo; el total del grafo es mucho mayor" : "")
          + " · toca un nodo para ver qué es y expandirlo";
        $("nodo-panel").hidden = true;
      })
      .catch(function (error) {
        $("mapa-pie").textContent = "No se pudo dibujar: " + error.message;
      });
  }

  function renderFilters() {
    var box = $("filtros-capa");
    var counts = state.graph.counts();
    var labels = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    box.innerHTML = "";
    if (!labels.length) { return; }
    labels.forEach(function (label) {
      var id = "capa-" + label;
      var input = el("input", { type: "checkbox", id: id, checked: "checked" });
      input.addEventListener("change", function () {
        state.graph.setLabelVisible(label, input.checked);
      });
      box.appendChild(el("label", { class: "capa", for: id }, [
        input,
        el("i", { style: "background:" + state.graph.colorFor(label) }),
        document.createTextNode(label + " (" + counts[label] + ")")
      ]));
    });
  }

  document.querySelectorAll("[data-mapa]").forEach(function (button) {
    button.addEventListener("click", function () {
      var action = button.dataset.mapa;
      if (action === "in") { state.graph.zoomBy(1.25); }
      if (action === "out") { state.graph.zoomBy(0.8); }
      if (action === "reset") { state.graph.reset(); }
      if (action === "clear") {
        state.graph.setData({ nodes: [], edges: [], seed: null });
        state.graph.hidden = {};
        $("filtros-capa").innerHTML = "";
        $("nodo-panel").hidden = true;
        $("mapa-pie").textContent = "Lienzo limpio. Elige un punto de partida arriba.";
      }
    });
  });

  $("form-buscar").addEventListener("submit", function (event) {
    event.preventDefault();
    var label = $("label-nodo").value;
    var text = $("q-nodo").value.trim();
    if (text.length < 2) {
      $("mapa-pie").textContent = "Escribe al menos dos letras para buscar.";
      return;
    }
    var results = $("resultados-busqueda");
    results.innerHTML = "";
    results.appendChild(el("span", { class: "pie", text: "Buscando " + text + "…" }));
    api("/api/search?label=" + encodeURIComponent(label) + "&texto=" + encodeURIComponent(text))
      .then(function (data) {
        results.innerHTML = "";
        var nodes = data.results || data.nodes || [];
        if (!nodes.length) {
          results.appendChild(el("span", {
            class: "pie",
            text: "No se encontró «" + text + "». Prueba con menos palabras, o con el RUT."
          }));
          return;
        }
        nodes.slice(0, 10).forEach(function (node) {
          var id = node.node || node.id || (label + "/" + (node.key || ""));
          var name = node.match || node.name || node.razon_social || node.nombre || id;
          results.appendChild(el("button", {
            class: "chip",
            text: name,
            onclick: function () { drawNode(id, 2, false); }
          }));
        });
        results.appendChild(el("span", { class: "pie", text: "Elige una para dibujar su entorno." }));
      })
      .catch(function (error) {
        results.innerHTML = "";
        results.appendChild(el("span", { class: "pie", text: "La búsqueda falló: " + error.message }));
      });
  });

  function openNode(node) {
    var body = $("nodo-panel-cuerpo");
    body.innerHTML = "";
    body.appendChild(el("h2", { text: node.name }));
    body.appendChild(el("p", { class: "badge graf", text: node.label }));

    var profileBox = el("div", { id: "perfil" }, [
      el("p", { class: "pie", text: "Leyendo los registros de esta entidad…" })
    ]);
    body.appendChild(profileBox);
    loadProfile(node.id);

    body.appendChild(el("h3", { text: "Qué puedes hacer" }));
    var actions = el("div", { class: "acciones" });
    actions.appendChild(el("button", {
      class: "chip",
      text: "Expandir un paso",
      onclick: function () { drawNode(node.id, 1, true); }
    }));
    actions.appendChild(el("button", {
      class: "chip",
      text: "Aislar este nodo",
      onclick: function () { drawNode(node.id, 1, false); }
    }));
    if (node.label === "Proveedor") {
      actions.appendChild(el("button", {
        class: "chip",
        text: "Ver su red de domicilios",
        onclick: function () { openNetwork(node.key); }
      }));
    }
    if (node.label === "Senal") {
      actions.appendChild(el("a", {
        class: "chip descarga",
        href: "/api/signals/" + node.key + ".pdf",
        text: "Descargar expediente (PDF)"
      }));
    }
    actions.appendChild(el("button", {
      class: "chip",
      text: "Investigar con el agente",
      onclick: function () {
        $("pregunta").value = "¿Qué se puede afirmar con los registros asociados a " + node.name
          + " (" + node.id + ")? Revisa sus órdenes, sus señales y su red de domicilios.";
        closePanels();
        show("investigar");
        $("pregunta").focus();
      }
    }));
    body.appendChild(actions);

    // Everything this node is connected to, with the count, and a list you can page
    // through. The canvas stays legible; the list is where the full set lives.
    var connections = el("div", { id: "conexiones" }, [
      el("h3", { text: "Conexiones" }),
      el("p", { class: "pie", text: "Contando…" })
    ]);
    body.appendChild(connections);
    loadConnections(node.id);
    // Opening a detector should land on its cases, not on a count you have to click.
    if (node.label === "Detector") {
      setTimeout(function () { loadConnectionPage(node.id, "PRODUCE", "out", 1, ""); }, 400);
    }

    $("nodo-panel").hidden = false;
  }

  var REL_LABEL = {
    EMITE: "emitió", ADJUDICADA_A: "adjudicada a", DERIVA_DE: "deriva de",
    CONVOCADA_POR: "convocada por", OFERTA_EN: "oferta en", GENERA: "genera",
    PUBLICA: "publica", EJECUTA: "ejecuta", SOCIO_DE: "socio de",
    REPRESENTA_A: "representa a", TIENE_DOMICILIO: "domicilio en",
    SOPORTADA_POR: "soportada por", EN: "está en", ACREDITA: "acredita",
    RESPALDA: "respalda", SE_OBSERVA_EN: "se observa en", PRODUCE: "produce",
    CORRELACIONA_CON: "se correlaciona con", CONTIENE: "contiene",
    CONTIENE_HALLAZGO: "contiene hallazgo", INVOLUCRA: "involucra",
    REDESCUBIERTO_POR: "redescubierto por", AFIRMA: "afirma"
  };

  function bar(value, max, cls) {
    var width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
    return el("span", { class: "barra " + (cls || "") }, [
      el("span", { class: "barra-fill", style: "width:" + width + "%" })
    ]);
  }

  function loadProfile(nodeId) {
    var box = $("perfil");
    if (!box) { return; }
    api("/api/profile?nodo=" + encodeURIComponent(nodeId))
      .then(function (data) {
        box.innerHTML = "";
        if (data.error) {
          box.appendChild(el("p", { class: "vacio", text: data.error }));
          return;
        }

        // --- the numbers a reader looks at first -------------------------
        var kpis = el("div", { class: "kpis" });
        function kpi(value, label, hint) {
          kpis.appendChild(el("div", { class: "kpi", title: hint || "" }, [
            el("span", { class: "kpi-n", text: value }),
            el("span", { class: "kpi-l", text: label })
          ]));
        }
        kpi(count(data.orders), "órdenes");
        kpi(shortMonto(data.total_clp), "monto total");
        kpi(shortMonto(data.average_clp), "promedio por orden");
        if (data.counterparty_label) {
          kpi(count(data.counterparties_total), data.counterparty_label === "Proveedor" ? "proveedores" : "organismos");
        }
        if (typeof data.hhi === "number") {
          kpi(count(data.hhi), "concentración (HHI)", "Sobre 2.500 se considera concentrado");
        }
        box.appendChild(kpis);

        // --- flags: what an auditor should look at -----------------------
        if (data.flags && data.flags.length) {
          var flags = el("div", { class: "banderas" });
          data.flags.forEach(function (flag) {
            flags.appendChild(el("div", { class: "bandera " + flag.level }, [
              el("strong", { text: flag.flag }),
              el("span", { text: flag.detail })
            ]));
          });
          box.appendChild(flags);
        }

        // --- history ------------------------------------------------------
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

        // --- procedures ---------------------------------------------------
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

        // --- who it deals with --------------------------------------------
        if (data.counterparties && data.counterparties.length) {
          box.appendChild(el("h3", { text: "Principales " + (data.counterparty_label === "Proveedor" ? "proveedores" : "compradores") }));
          var maxCp = Math.max.apply(null, data.counterparties.map(function (c) { return c.orders; }));
          var list = el("div", { class: "anios" });
          data.counterparties.forEach(function (party) {
            var row = el("div", { class: "anio clickable" }, [
              el("span", { class: "anio-l ancho", text: String(party.name).slice(0, 34) }),
              bar(party.orders, maxCp),
              el("span", { class: "anio-v", text: count(party.orders) })
            ]);
            row.addEventListener("click", function () { closePanels(); drawNode(party.node, 1, false); });
            list.appendChild(row);
          });
          box.appendChild(list);
        }

        // --- address network ----------------------------------------------
        if (data.network_peers && data.network_peers.length) {
          box.appendChild(el("h3", { text: "Mismo domicilio registrado" }));
          data.network_peers.forEach(function (peer) {
            box.appendChild(el("div", { class: "fuente-item" }, [
              el("span", { class: "dom", text: peer.razon_social || peer.rut }),
              el("span", { text: " · " + count(peer.orders) + " órdenes · RUT " + peer.rut })
            ]));
          });
          box.appendChild(el("p", {
            class: "pie",
            text: "Un domicilio compartido también es lo que parece un edificio de oficinas: es una pista, no una relación afirmada."
          }));
        }

        // --- the system's own patterns ------------------------------------
        if (data.signals && data.signals.length) {
          box.appendChild(el("h3", { text: "Señales que la mencionan" }));
          data.signals.forEach(function (signal) {
            var item = el("button", {
              class: "item-fila",
              onclick: function () { closePanels(); drawNode("Senal/" + signal.id, 1, false); }
            }, [
              el("span", { class: "item-nombre", text: String(signal.claim).slice(0, 120) }),
              el("span", { class: "item-meta", text: signal.detector + " · " + count(signal.ordenes) + " órdenes" })
            ]);
            box.appendChild(item);
          });
        }

        if (data.claim) {
          box.appendChild(el("h3", { text: "La afirmación" }));
          box.appendChild(el("p", { class: "lead small", text: data.claim }));
        }

        if (data.note) {
          box.appendChild(el("p", { class: "pie", text: data.note }));
        }
      })
      .catch(function (error) {
        box.innerHTML = "";
        box.appendChild(el("p", { class: "descarte", text: "No se pudo leer el perfil: " + error.message }));
      });
  }

  function loadConnections(nodeId) {
    var box = $("conexiones");
    if (!box) { return; }
    api("/api/connections?nodo=" + encodeURIComponent(nodeId))
      .then(function (data) {
        box.innerHTML = "";
        box.appendChild(el("h3", { text: "Conexiones" }));
        var list = data.connections || [];
        if (!list.length) {
          box.appendChild(el("p", { class: "vacio", text: "Este nodo no tiene relaciones registradas." }));
          return;
        }
        list.forEach(function (connection) {
          var row = el("div", { class: "conexion" }, [
            el("div", {}, [
              el("span", { class: "rel-tag", text: (REL_LABEL[connection.rel] || connection.rel) }),
              el("span", { class: "rel-target", text: connection.direction === "in"
                ? "← " + connection.other
                : "→ " + connection.other }),
              el("span", { class: "rel-count", text: count(connection.count) })
            ]),
            el("button", {
              class: "chip",
              text: "Ver lista",
              onclick: function () {
                loadConnectionPage(nodeId, connection.rel, connection.direction, 1);
              }
            })
          ]);
          box.appendChild(row);
        });
      })
      .catch(function (error) {
        box.innerHTML = "";
        box.appendChild(el("p", { class: "descarte", text: "No se pudieron leer las conexiones: " + error.message }));
      });
  }

  function loadConnectionPage(nodeId, rel, direction, page, orden) {
    var box = $("conexiones");
    box.innerHTML = "";
    box.appendChild(el("h3", { text: REL_LABEL[rel] || rel }));
    box.appendChild(el("p", { class: "pie", text: "Cargando…" }));

    var query = "/api/connection-page?nodo=" + encodeURIComponent(nodeId)
      + "&rel=" + encodeURIComponent(rel)
      + "&direction=" + encodeURIComponent(direction)
      + "&page=" + (page || 1) + "&size=25"
      + (orden ? "&orden=" + encodeURIComponent(orden) : "");

    api(query).then(function (data) {
      box.innerHTML = "";
      var head = el("div", { class: "lista-head" }, [
        el("button", { class: "link", text: "← volver a las conexiones", onclick: function () { loadConnections(nodeId); } }),
        el("span", { class: "pie", text: count(data.total) + " en total · página " + data.page + " de " + data.pages })
      ]);
      box.appendChild(head);

      var controls = el("div", { class: "acciones" });
      [["Más grandes", "monto"], ["Más recientes", "fecha"], ["Orden del grafo", ""]].forEach(function (pair) {
        controls.appendChild(el("button", {
          class: "chip" + (data.orden === (pair[1] || "grafo") ? " activo" : ""),
          text: pair[0],
          onclick: function () { loadConnectionPage(nodeId, rel, direction, 1, pair[1]); }
        }));
      });
      box.appendChild(controls);

      var list = el("div", { class: "lista" });
      (data.items || []).forEach(function (item) {
        var bits = [];
        if (item.monto_clp) { bits.push(shortMonto(item.monto_clp)); }
        if (item.fecha) { bits.push(String(item.fecha).slice(0, 10)); }
        if (item.procedencia) { bits.push(item.procedencia); }
        if (item.severidad) { bits.push(item.severidad); }
        var row = el("button", {
          class: "item-fila",
          onclick: function () { closePanels(); drawNode(item.id, 1, false); }
        }, [
          el("span", { class: "item-nombre", text: String(item.name).slice(0, 90) }),
          el("span", { class: "item-meta", text: bits.join(" · ") }),
          el("span", { class: "badge", text: item.label })
        ]);
        list.appendChild(row);
      });
      box.appendChild(list);

      if (data.pages > 1) {
        var nav = el("div", { class: "acciones" });
        if (data.page > 1) {
          nav.appendChild(el("button", { class: "chip", text: "← anteriores", onclick: function () {
            loadConnectionPage(nodeId, rel, direction, data.page - 1, orden);
          }}));
        }
        if (data.page < data.pages) {
          nav.appendChild(el("button", { class: "chip", text: "siguientes →", onclick: function () {
            loadConnectionPage(nodeId, rel, direction, data.page + 1, orden);
          }}));
        }
        box.appendChild(nav);
      }
    }).catch(function (error) {
      box.innerHTML = "";
      box.appendChild(el("p", { class: "descarte", text: "No se pudo leer la lista: " + error.message }));
    });
  }

  function openNetwork(rut) {
    var body = $("nodo-panel-cuerpo");
    api("/api/network?rut=" + encodeURIComponent(rut)).then(function (data) {
      body.innerHTML = "";
      body.appendChild(el("h2", { text: data.proveedor || rut }));
      body.appendChild(el("p", { class: "badge graf", text: "Red de domicilios" }));
      if (data.domicilio) {
        body.appendChild(el("p", {
          text: (data.domicilio.direccion || "") + (data.domicilio.region ? " · " + data.domicilio.region : "")
        }));
      }
      var peers = data.peers || [];
      if (!peers.length) {
        body.appendChild(el("p", { class: "vacio", text: "No aparece otra empresa en el mismo domicilio registrado." }));
      } else {
        body.appendChild(el("h3", { text: peers.length + " empresa(s) en el mismo domicilio" }));
        peers.forEach(function (peer) {
          body.appendChild(el("div", { class: "fuente-item" }, [
            el("span", { class: "dom", text: peer.razon_social || peer.rut }),
            el("span", { text: " · " + count(peer.orders) + " órdenes · RUT " + peer.rut })
          ]));
        });
      }
      if (data.not_claimed) {
        body.appendChild(el("p", { class: "pie", text: "Ojo: " + data.not_claimed }));
      }
      $("nodo-panel").hidden = false;
    }).catch(function (error) {
      body.appendChild(el("p", { class: "descarte", text: "No se pudo leer la red: " + error.message }));
    });
  }

  // showNode is kept for the signal card, which jumps from the board to the map.
  function showNode(nodeId) { drawNode(nodeId, 2, false); }

  // --------------------------------------------------------------- investigar
  document.querySelectorAll(".sugerencias .chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      $("pregunta").value = chip.textContent.trim();
      $("pregunta").focus();
    });
  });

  $("form-investigar").addEventListener("submit", function (event) {
    event.preventDefault();
    var question = $("pregunta").value.trim();
    if (question.length < 8) { return; }

    $("btn-investigar").disabled = true;
    $("btn-investigar").textContent = "Investigando…";
    $("agente").hidden = false;
    state.events = [];
    $("agente-plan").innerHTML = "";
    $("agente-pasos").innerHTML = "";
    $("agente-afirmaciones").innerHTML = "";
    $("agente-descartes").innerHTML = "";
    $("agente-fuentes").innerHTML = "";
    $("agente-estado").textContent = "El sistema está leyendo los datos. Los pasos aparecen a medida que ocurren…";

    api("/api/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question })
    }).then(function (data) {
      state.runId = data.run_id;
      followRun(data.run_id);
    }).catch(function (error) {
      $("agente-estado").textContent = "No se pudo empezar: " + error.message;
      $("btn-investigar").disabled = false;
      $("btn-investigar").textContent = "Investigar";
    });
  });

  function followRun(runId) {
    if (state.stream) { state.stream.close(); }
    var source = new EventSource("/api/runs/" + runId + "/stream");
    state.stream = source;

    source.onmessage = function (message) {
      var event = JSON.parse(message.data);
      if (event.type === "stream.end") {
        source.close();
        finishRun(runId);
        return;
      }
      state.events.push(event);
      applyEvent(event);
    };

    source.onerror = function () {
      source.close();
      finishRun(runId);
    };
  }

  function applyEvent(event) {
    if (event.type === "agent.plan") {
      var plan = $("agente-plan");
      plan.innerHTML = "";
      (event.payload.steps || []).forEach(function (step) {
        plan.appendChild(el("li", { text: step }));
      });
    }
    if (event.type === "agent.step") {
      var steps = $("agente-pasos");
      steps.appendChild(el("li", {}, [
        el("span", { class: "n", text: String(event.payload.n) }),
        el("div", {}, [
          el("span", { class: "tool", text: event.payload.tool }),
          el("div", { text: event.payload.observation || event.payload.summary || "" })
        ])
      ]));
      steps.lastChild.scrollIntoView({ block: "nearest" });
    }
    if (event.type === "claim.accepted") {
      var ok = $("agente-afirmaciones");
      var box = el("div", { class: "afirmacion" }, [el("div", { text: event.payload.texto })]);
      if (event.payload.cita) {
        box.appendChild(el("blockquote", { text: "«" + event.payload.cita + "»" }));
        box.appendChild(el("span", {
          class: "fuente",
          text: (event.payload.dominio || "fuente") + " · " + (event.payload.url || "")
        }));
      } else {
        box.appendChild(el("span", {
          class: "fuente",
          text: "respaldada por: " + (event.payload.nodos || []).join(", ")
        }));
      }
      ok.appendChild(box);
    }
    if (event.type === "claim.rejected") {
      $("agente-descartes").appendChild(el("div", { class: "descarte" }, [
        el("div", { text: event.payload.text || "" }),
        el("span", { class: "motivo", text: "No se sostuvo: " + (event.payload.reason || "sin cita") })
      ]));
    }
    if (event.type === "source.added") {
      $("agente-fuentes").appendChild(el("div", { class: "fuente-item" }, [
        el("span", { class: "dom", text: event.payload.title || event.payload.url }),
        el("span", { text: " · " + event.payload.url })
      ]));
    }
    if (event.type === "budget.exceeded") {
      $("agente-estado").textContent = "La investigación llegó al límite de " + event.payload.limit
        + " (" + event.payload.max + ") y se cerró con lo que alcanzó a sostener.";
    }
    if (event.type === "run.failed") {
      $("agente-estado").textContent = "La investigación se cortó: " + (event.payload.error || "error desconocido");
    }
  }

  function finishRun(runId) {
    $("btn-investigar").disabled = false;
    $("btn-investigar").textContent = "Investigar otra vez";
    api("/api/runs/" + runId).then(function (data) {
      var state_ = data.state || {};
      var claims = (state_.claims_accepted || []).length;
      var rejected = (state_.claims_rejected || []).length;
      $("agente-estado").textContent = "Listo. " + claims + " afirmación(es) con respaldo y "
        + rejected + " descartada(s). El expediente se puede descargar y firmar."
        + (state_.finished && state_.finished.stopped_by ? " Se cerró por: " + state_.finished.stopped_by + "." : "");
      if (!claims && !rejected) {
        $("agente-afirmaciones").appendChild(el("p", {
          class: "vacio",
          text: "No hubo ninguna afirmación que resistiera el control de evidencia. Es un resultado válido: "
            + "significa que lo revisado no alcanzó para sostener algo, no que no se haya mirado."
        }));
      }
      $("descargar-expediente").onclick = function () {
        var blob = new Blob([data.dossier || ""], { type: "text/markdown;charset=utf-8" });
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "expediente-" + runId + ".md";
        link.click();
      };
      $("ver-registro").onclick = function () { openLog(runId); };
    });
  }

  $("ver-registro").addEventListener("click", function () {
    if (state.runId) { openLog(state.runId); }
  });

  // ---------------------------------------------------------------- registro
  var EVENT_LABEL = {
    "run.started": "Investigación iniciada",
    "run.finished": "Investigación cerrada",
    "run.failed": "Investigación interrumpida",
    "agent.plan": "Plan",
    "agent.step": "Paso",
    "model.request": "Consulta al modelo",
    "model.response": "Respuesta del modelo",
    "tool.call": "Herramienta",
    "tool.result": "Resultado",
    "tool.error": "Error de herramienta",
    "budget.exceeded": "Límite alcanzado",
    "source.added": "Fuente descargada",
    "source.rejected": "Fuente rechazada",
    "claim.accepted": "Afirmación aceptada",
    "claim.rejected": "Afirmación descartada",
    "dossier.built": "Expediente generado"
  };

  var IMPORTANT = {
    "agent.plan": 1, "agent.step": 1, "tool.call": 1, "claim.accepted": 1, "claim.rejected": 1,
    "source.added": 1, "source.rejected": 1, "budget.exceeded": 1, "dossier.built": 1,
    "run.started": 1, "run.finished": 1, "run.failed": 1
  };

  function eventKind(type) {
    if (type === "claim.accepted") { return "claim_ok"; }
    if (type === "claim.rejected") { return "claim_no"; }
    if (type === "budget.exceeded") { return "budget"; }
    if (type.indexOf("tool.") === 0 || type === "agent.step") { return "tool"; }
    return "";
  }

  function detailOf(event) {
    var payload = event.payload || {};
    switch (event.type) {
      case "agent.plan": return (payload.steps || []).join(" · ");
      case "agent.step": return payload.tool + " — " + (payload.observation || payload.summary || "");
      case "tool.call": return payload.tool + "(" + Object.keys(payload.arguments || {}).map(function (key) {
        return key + "=" + JSON.stringify(payload.arguments[key]);
      }).join(", ") + ")";
      case "tool.result": return (payload.summary || "") + " · " + (payload.duration_ms || 0) + " ms";
      case "tool.error": return payload.tool + ": " + payload.error;
      case "claim.accepted": return payload.texto + (payload.cita ? " — «" + payload.cita.slice(0, 120) + "»" : "");
      case "claim.rejected": return (payload.text || "") + " — " + payload.reason;
      case "source.added": return (payload.title || "") + " · " + payload.url;
      case "source.rejected": return payload.url + " — " + payload.reason;
      case "budget.exceeded": return "límite de " + payload.limit + ": " + payload.max;
      case "dossier.built": return (payload.id || "") + " · " + payload.claims + " afirmación(es)";
      case "run.started": return payload.question || "";
      case "run.finished": return (payload.claims || 0) + " afirmación(es) · " + (payload.sources || 0)
        + " fuente(s) · " + (payload.steps || 0) + " paso(s) · " + (payload.wall_s || 0) + " s";
      case "run.failed": return payload.error || "";
      case "model.response": return "tokens: " + JSON.stringify(payload.usage || {}).slice(0, 90);
      default: return JSON.stringify(payload).slice(0, 160);
    }
  }

  function loadRunList() {
    api("/api/runs?limit=30").then(function (data) {
      var select = $("selector-run");
      select.innerHTML = "";
      (data.runs || []).forEach(function (run) {
        select.appendChild(el("option", {
          value: run.id,
          text: (run.question || run.id).slice(0, 80)
        }));
      });
      if (data.runs && data.runs.length) {
        if (!state.logRun) { state.logRun = data.runs[0].id; }
        select.value = state.logRun;
        loadLog(state.logRun);
      }
    });
  }

  $("selector-run").addEventListener("change", function (event) {
    state.logRun = event.target.value;
    loadLog(state.logRun);
  });
  $("solo-importantes").addEventListener("change", renderLog);
  $("seguir-vivo").addEventListener("change", function () {
    if (this.checked && state.logRun) { followLog(state.logRun); }
    else if (state.logStream) { state.logStream.close(); }
  });

  function openLog(runId) {
    state.logRun = runId;
    show("registro");
    loadRunListThenFollow(runId);
  }

  function loadRunListThenFollow(runId) {
    api("/api/runs?limit=30").then(function (data) {
      var select = $("selector-run");
      select.innerHTML = "";
      var found = false;
      (data.runs || []).forEach(function (run) {
        if (run.id === runId) { found = true; }
        select.appendChild(el("option", { value: run.id, text: (run.question || run.id).slice(0, 80) }));
      });
      if (!found) {
        select.appendChild(el("option", { value: runId, text: runId }));
      }
      select.value = runId;
      state.logRun = runId;
      loadLog(runId);
    });
  }

  function loadLog(runId) {
    if (state.logStream) { state.logStream.close(); state.logStream = null; }
    state.events = [];
    api("/api/runs/" + runId).then(function (data) {
      state.events = data.events || [];
      renderLog();
      var runState = data.state || {};
      var finished = runState.finished || runState.failed;
      if (!finished && $("seguir-vivo").checked) { followLog(runId); }
      else {
        $("log-resumen").textContent = state.events.length + " eventos · expediente "
          + ((runState.dossier || {}).id || "—") + " · huella " + (data.digest || "").slice(0, 12);
      }
    }).catch(function (error) {
      $("log-resumen").textContent = "No se pudo leer el registro: " + error.message;
    });
  }

  function followLog(runId) {
    if (state.logStream) { state.logStream.close(); }
    var source = new EventSource("/api/runs/" + runId + "/stream?since=" + state.events.length);
    state.logStream = source;
    source.onmessage = function (message) {
      var event = JSON.parse(message.data);
      if (event.type === "stream.end") { source.close(); return; }
      state.events.push(event);
      renderLog();
    };
    source.onerror = function () { source.close(); };
  }

  function renderLog() {
    var body = $("logs-cuerpo");
    var onlyImportant = $("solo-importantes").checked;
    body.innerHTML = "";
    state.events.filter(function (event) {
      return !onlyImportant || IMPORTANT[event.type];
    }).forEach(function (event) {
      body.appendChild(el("tr", { "data-kind": eventKind(event.type) }, [
        el("td", { class: "seq", text: String(event.seq) }),
        el("td", { class: "hora", text: hhmmss(event.ts) }),
        el("td", { class: "que", text: EVENT_LABEL[event.type] || event.type }),
        el("td", { class: "det", text: detailOf(event) })
      ]));
    });
    $("log-resumen").textContent = state.events.length + " eventos en el registro";
  }

  // ---------------------------------------------------------------- arranque
  show("inicio");
})();
