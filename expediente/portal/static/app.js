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
    onSelect: function (node) { openCard(node.id, node.name, node.label); }
  });

  document.querySelectorAll("[data-mapa]").forEach(function (button) {
    button.addEventListener("click", function () {
      var action = button.dataset.mapa;
      if (action === "in") { state.graph.zoomBy(1.25); }
      if (action === "out") { state.graph.zoomBy(0.8); }
      if (action === "reset") { state.graph.reset(); closeCard(); }
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

  $("btn-capas").addEventListener("click", function () {
    var box = $("capas");
    box.hidden = !box.hidden;
    $("btn-capas").setAttribute("aria-expanded", String(!box.hidden));
  });

  // ------------------------------------------------------------------- runs
  function followRun(runId) {
    state.runId = runId;
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
      } else if (event.type === "tool.result") {
        step(box, "·", event.payload.summary || "", "");
      } else if (event.type === "claim.accepted") {
        step(box, "afirmó", event.payload.texto, "ok");
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
  $("btn-registro").addEventListener("click", function () { openLog(state.runId); });
  $("cerrar-registro").addEventListener("click", function () { $("registro").hidden = true; });
  $("solo-importantes").addEventListener("change", renderEvents);

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

  function renderEvents() {
    var list = $("eventos");
    var onlyImportant = $("solo-importantes").checked;
    list.innerHTML = "";
    state.events.filter(function (event) {
      return !onlyImportant || IMPORTANTES[event.type];
    }).forEach(function (event) {
      var kind = event.type === "claim.accepted" ? "ok" : event.type === "claim.rejected" || event.type === "budget.exceeded" ? "no"
        : event.type.indexOf("tool.") === 0 ? "tool" : "";
      var detail = "";
      var payload = event.payload || {};
      if (event.type === "agent.plan") { detail = (payload.steps || []).join(" · "); }
      else if (event.type === "tool.result") { detail = (payload.summary || "") + " · " + (payload.duration_ms || 0) + " ms"; }
      else if (event.type === "tool.call") { detail = payload.tool + "(" + JSON.stringify(payload.arguments || {}).slice(0, 90) + ")"; }
      else if (event.type === "claim.accepted") { detail = payload.texto; }
      else if (event.type === "claim.rejected") { detail = (payload.text || "") + " — " + payload.reason; }
      else if (event.type === "source.added") { detail = payload.url; }
      else if (event.type === "run.finished") { detail = (payload.claims || 0) + " afirmaciones · " + (payload.steps || 0) + " pasos"; }
      list.appendChild(el("li", { class: kind }, [
        el("span", { class: "n", text: String(event.seq) }),
        el("div", {}, [
          el("span", { class: "que", text: ETIQUETA[event.type] || event.type }),
          el("span", { class: "det", text: detail })
        ])
      ]));
    });
  }

  boot();
})();
