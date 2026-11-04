/* El grafo: un solo lienzo force-directed, y los verbos sobre las aristas.
 *
 * ## Por qué cambió el motor (y qué se conservó)
 *
 * La versión anterior dibujaba a mano sobre un `<canvas>` con un layout de anillos: el detector
 * al centro, la señal en el primer anillo, la orden en el segundo y el documento afuera. Era
 * correcto y legible, pero se leía como un diagrama orbital, no como una red. La lectura que
 * importa en el pasillo —"esto es un sistema vivo de relaciones"— no ocurría.
 *
 * Ahora el layout es **force-directed (fcose)**: los nodos se repelen, las aristas tiran. Es el
 * mismo motor y el mismo tema que el explorador de la memoria del agente, y la razón es simple:
 * la forma tiene que decir "red", no "órbita".
 *
 * ## La regla que este archivo sigue sin romper
 *
 * Cada cambio visual está respaldado por un dato que existe. Un nodo se ilumina porque la arista
 * está; una afirmación se pinta roja porque la grabación dice que fue rechazada; una ruta se
 * dibuja salto por salto porque hay tantos pasos grabados como saltos. No hay animación decorativa
 * que sugiera trabajo que no ocurrió: el layout es geometría, no una promesa.
 *
 * ## Lo que el mapa sigue declarando
 *
 * Un dibujo recortado lo dice. El servidor manda `truncated` y el lienzo lo escribe: la ventana es
 * una ventana, y el total es mucho más grande. Un grafo que finge ser el corpus entero es una
 * mentira útil hasta que alguien la usa para decidir.
 */
(function (global) {
  "use strict";

  //: Un color por tipo de nodo. Es la misma paleta que usa la leyenda y las capas: si cambia acá,
  //: cambia en los dos lugares, porque las dos leen de `colorFor`.
  var COLORS = {
    Detector: "#8b5cf6",
    Senal: "#22d3ee",
    OrdenCompra: "#64748b",
    Proveedor: "#34d399",
    Organismo: "#fbbf24",
    Domicilio: "#f87171",
    Documento: "#60a5fa",
    Evidencia: "#60a5fa",
    Afirmacion: "#a78bfa",
    Licitacion: "#2dd4bf",
    CompraAgil: "#2dd4bf",
    Persona: "#fb923c"
  };

  //: Formas: solo tres, y con motivo. Una señal es un rombo porque es un juicio, no una entidad;
  //: un organismo es un rectángulo porque es una casilla; el resto son círculos.
  //: Todos los tipos se dibujan igual: círculo. Las formas geométricas distintas (rombo, hexágono,
  //: rectángulo) convierten una red en un diagrama de flujo, y el grafo de referencia —el de la
  //: memoria— no las usa. La diferencia entre tipos la llevan el color y la etiqueta.
  //: El test de accesibilidad sigue exigiendo una entrada por tipo: la tabla está completa.
  var SHAPES = {
    Detector: "ellipse",
    Senal: "ellipse",
    OrdenCompra: "ellipse",
    Proveedor: "ellipse",
    Organismo: "ellipse",
    Domicilio: "ellipse",
    Documento: "ellipse",
    Evidencia: "ellipse",
    Afirmacion: "ellipse",
    Licitacion: "ellipse",
    CompraAgil: "ellipse",
    Persona: "ellipse"
  };

  //: El tamaño base por tipo, antes de sumar el grado. El detector manda en la escena.
  var BASE_SIZE = {
    Detector: 46,
    Senal: 28,
    Proveedor: 24,
    Organismo: 24,
    Domicilio: 20,
    Persona: 20,
    Afirmacion: 22,
    Documento: 18,
    Evidencia: 18,
    OrdenCompra: 13,    //: una orden es volumen, no hallazgo: se dibuja, pero no compite
    CompraAgil: 14,
    Licitacion: 14
  };

  //: No hay recorte de foco. El corpus entero vive en el lienzo y lo que se mueve es la cámara:
  //: esconder 800 nodos para que se lea es una decisión que el lector no tomó.

  var REL_ES = {
    EMITE: "emite",
    ADJUDICADA_A: "adjudicada a",
    ACREDITA: "acredita",
    SOPORTADA_POR: "soportada por",
    EN: "contenida en",
    COMPARTE_DOMICILIO: "comparte domicilio",
    COMPARTE_REPRESENTANTE: "comparte representante",
    TIENE_DOMICILIO: "tiene domicilio",
    REPRESENTADA_POR: "representada por",
    OBSERVA: "observa",
    GENERA: "genera",
    PARTICIPA_EN: "participa en",
    COTIZA_EN: "cotiza en",
    PERTENECE_A: "pertenece a"
  };

  var LABELS_ES = {
    Detector: "detector",
    Senal: "señal",
    OrdenCompra: "orden de compra",
    CompraAgil: "compra ágil",
    Licitacion: "licitación",
    Proveedor: "proveedor",
    Organismo: "organismo",
    Domicilio: "domicilio",
    Persona: "persona",
    Documento: "documento",
    Evidencia: "evidencia",
    Afirmacion: "afirmación"
  };

  //: Aclara u oscurece un color hex. Sirve para el degradado radial que convierte un círculo
  //: plano en una esfera: sin luz, un nodo es una calcomanía.
  function shade(hex, amount) {
    var value = String(hex).replace("#", "");
    if (value.length === 3) {
      value = value[0] + value[0] + value[1] + value[1] + value[2] + value[2];
    }
    var number = parseInt(value, 16);
    var r = (number >> 16) & 255;
    var g = (number >> 8) & 255;
    var b = number & 255;
    var mix = function (channel) {
      var out = amount > 0
        ? channel + (255 - channel) * amount
        : channel * (1 + amount);
      return Math.max(0, Math.min(255, Math.round(out)));
    };
    return "#" + [mix(r), mix(g), mix(b)].map(function (channel) {
      return ("0" + channel.toString(16)).slice(-2);
    }).join("");
  }

  function truncate(text, max) {
    var value = String(text === undefined || text === null ? "" : text);
    return value.length > max ? value.slice(0, max - 1) + "…" : value;
  }

  function GraphView(container, options) {
    this.container = container;
    this.options = options || {};
    this.nodes = [];
    this.edges = [];
    this.selected = null;
    this.pinned = [];
    this.hidden = {};
    this.solo = null;
    this.peek = [];
    this.rechazo = [];
    this.mode = "2d";          //: se conserva por compatibilidad: ya no hay segunda cámara
    this._hover = null;
    this._legend = null;
    this._tip = null;
    this._ready = false;
    this._build();
    this._bind();
    if (this.options.legend) { this._mountLegend(); }
  }

  GraphView.prototype.colorFor = function (label) {
    return COLORS[label] || "#94a3b8";
  };

  GraphView.prototype._build = function () {
    var self = this;
    if (!global.cytoscape) {
      //: Sin la librería no se dibuja, pero el panel de nodo y el registro siguen funcionando.
      //: Un lienzo vacío con un error de consola es mejor que una página en blanco sin pistas.
      if (global.console) { global.console.error("Expediente: falta cytoscape.min.js en /static/vendor/"); }
      return;
    }
    if (global.cytoscapeFcose && !global.cytoscape.__fcoseRegistered) {
      try {
        global.cytoscape.use(global.cytoscapeFcose);
        global.cytoscape.__fcoseRegistered = true;
      } catch (error) { /* ya registrado: no es un problema */ }
    }

    this.cy = global.cytoscape({
      container: this.container,
      minZoom: 0.08,
      maxZoom: 3.4,
      boxSelectionEnabled: false,
      selectionType: "single",
      autoungrabify: false,
      pixelRatio: "auto",
      hideEdgesOnViewport: true,
      style: [
        { selector: "core", style: {
          "background-color": "transparent",
          "active-bg-opacity": 0,
          "selection-box-opacity": 0
        } },
        { selector: "node", style: {
          "background-color": "data(color)",
          "background-opacity": 1,
          "width": "data(size)",
          "height": "data(size)",
          "shape": "data(shape)",
          "border-width": 1,
          "border-color": "rgba(255,255,255,0.28)",
          "border-opacity": 1,
          //: Sin halo difuso. El "volumen" del grafo de la memoria no venía de sombras (este
          //: Cytoscape no soporta shadow-*), sino de un borde claro de 1 px que simula luz
          //: sobre el canto. El halo del mismo color lo único que hacía era engordar el punto.
          "overlay-opacity": 0,
          "label": "data(label)",
          "font-size": 10,
          "font-family": "system-ui, sans-serif",
          "font-weight": 500,
          "color": "#e2e8f0",
          "text-opacity": 0,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 3,
          "text-outline-width": 2.5,
          "text-outline-color": "#07090d",
          "text-outline-opacity": 0.95,
          //: La pastilla es el ancla: sin fondo, una etiqueta en un campo oscuro flota y el ojo
          //: no sabe a qué nodo pertenece. Con fondo, es una etiqueta pegada a su nodo.
          "text-background-color": "#16233b",
          "text-background-opacity": 0.9,
          "text-background-padding": 3,
          "text-background-shape": "roundrectangle",
          "text-max-width": "150px",
          "text-wrap": "ellipsis",
          "min-zoomed-font-size": 10,
          "transition-property": "opacity, border-color, border-width, background-color",
          "transition-duration": "150ms"
        } },
        //: Solo los hubs llevan etiqueta permanente. Con 70 nombres encendidos no se lee ninguno.
        { selector: "node[deg >= 8]", style: { "text-opacity": 1 } },
        { selector: "node.lbl", style: { "text-opacity": 1 } },
        //: Una señal con grado 1 sólo está colgada de su detector: no tiene órdenes ni documentos
        //: cargados. Son 480 de las 565 señales y forman las islas que se veían como "grupos
        //: aislados". No se borran (son reales), se atenúan y se declaran en el pie.
        { selector: 'node[kind = "Senal"][deg = 1]', style: {
          opacity: 0.18, "text-opacity": 0, "border-opacity": 0.2
        } },
        { selector: 'node[kind = "OrdenCompra"]', style: {
          "background-opacity": 0.55, "overlay-opacity": 0.05, "border-opacity": 0.55
        } },
        { selector: "edge", style: {
          "width": 0.9,
          "line-color": "rgba(130,160,200,0.34)",
          "opacity": 0.55,
          "curve-style": "bezier",
          "target-arrow-shape": "none",
          "overlay-opacity": 0,
          "transition-property": "opacity, line-color, width",
          "transition-duration": "150ms"
        } },
        //: Las aristas que son el hallazgo (domicilio o representante compartido) se ven distinto
        //: sin que nadie tenga que abrir el panel: rojo y punteado.
        { selector: 'edge[rel = "COMPARTE_DOMICILIO"]', style: {
          "line-color": "rgba(248,113,113,0.55)", "width": 1.8, "line-style": "dashed"
        } },
        { selector: 'edge[rel = "COMPARTE_REPRESENTANTE"]', style: {
          "line-color": "rgba(248,113,113,0.55)", "width": 1.8, "line-style": "dashed"
        } },
        //: La lente de vecindad: al pasar el mouse, el resto del grafo se apaga al 12%.
        { selector: ".faded", style: { opacity: 0.12, "text-opacity": 0 } },
        { selector: "edge.faded", style: { opacity: 0.05 } },
        { selector: "node.hover", style: { opacity: 1, "text-opacity": 1 } },
        { selector: "node.sel", style: {
          "border-width": 3, "border-color": "#ffffff", "text-opacity": 1, opacity: 1
        } },
        //: Las aristas del nodo elegido se encienden: si el panel dice "comparte domicilio",
        //: esa arista tiene que estar visible sin buscarla.
        { selector: "edge.hl", style: {
          "line-color": "rgba(148,163,184,0.75)", width: 1.8, opacity: 1
        } },
        { selector: "node.pin", style: { "border-width": 3.2, "border-color": "#fde047" } },
        { selector: "node.route", style: {
          opacity: 1, "text-opacity": 1, "border-width": 2.4, "border-color": "#22d3ee"
        } },
        { selector: "edge.route", style: {
          "line-color": "rgba(34,211,238,0.85)", width: 2.4, opacity: 1
        } },
        { selector: "node.rejected", style: {
          "background-color": "#7f1d1d", "border-width": 3, "border-color": "#f87171",
          opacity: 1, "text-opacity": 1
        } },
        { selector: "node.peek", style: { opacity: 1, "text-opacity": 1 } },
        { selector: "node.pulse", style: { "border-width": 4, "border-color": "#fde047" } },
        { selector: "node.oculto", style: { display: "none" } },
        { selector: "edge.oculto", style: { display: "none" } }
      ]
    });

    this._ready = true;
  };

  GraphView.prototype._bind = function () {
    var self = this;
    if (!this._ready) { return; }

    this.cy.on("tap", "node", function (event) {
      var node = event.target;
      var id = node.id();
      self.selected = id;
      self._paint();
      if (self.options.onSelect) {
        self.options.onSelect({ id: id, name: node.data("name"), label: node.data("kind") });
      }
    });

    this.cy.on("tap", function (event) {
      if (event.target !== self.cy) { return; }
      self.selected = null;
      self._paint();
    });

    //: El doble clic es "traeme el vecindario de este nodo": el gesto que en el mapa anterior
    //: abría la expansión, ahora sobre el mismo nodo.
    this.cy.on("dbltap", "node", function (event) {
      if (self.options.onExpand) {
        var node = event.target;
        self.options.onExpand({ id: node.id(), name: node.data("name"), label: node.data("kind") });
      }
    });

    this.cy.on("mouseover", "node", function (event) {
      var node = event.target;
      self._hover = node.id();
      self._lens(node);
      self._showTip(node);
    });

    this.cy.on("mousemove", "node", function (event) {
      self._moveTip(event.originalEvent || event.renderedPosition);
    });

    this.cy.on("mouseout", "node", function () {
      self._hover = null;
      self._hideTip();
      self._clearLens();
    });

    //: Un grafo que se puede arrastrar fuera de la pantalla es un grafo que se pierde.
    this.cy.on("zoom", function () {
      if (self.cy.zoom() < 0.08) { self.cy.zoom(0.08); }
    });
  };

  /* ------------------------------------------------------------------ lente */

  GraphView.prototype._lens = function (node) {
    var nb = node.closedNeighborhood();
    this.cy.elements().not(nb).addClass("faded");
    node.addClass("hover");
    nb.nodes().filter(function (other) { return other.id() !== node.id(); })
      .addClass("peek");
  };

  GraphView.prototype._clearLens = function () {
    this.cy.elements().removeClass("faded");
    this.cy.elements().removeClass("hover");
    this.cy.elements().removeClass("peek");
    this._paint();
  };

  GraphView.prototype._showTip = function (node) {
    if (!this._tip) {
      this._tip = document.createElement("div");
      this._tip.className = "grafo-tip";
      this.container.appendChild(this._tip);
    }
    var name = node.data("name") || node.id();
    var kind = LABELS_ES[node.data("kind")] || node.data("kind");
    this._tip.innerHTML = "";
    var strong = document.createElement("b");
    strong.textContent = truncate(name, 64);
    var small = document.createElement("span");
    small.textContent = kind + " · " + node.connectedEdges().length + " relaciones";
    this._tip.appendChild(strong);
    this._tip.appendChild(small);
    this._tip.style.opacity = "1";
  };

  GraphView.prototype._moveTip = function (event) {
    if (!this._tip || !event) { return; }
    var rect = this.container.getBoundingClientRect();
    var x = (event.clientX || 0) - rect.left;
    var y = (event.clientY || 0) - rect.top;
    this._tip.style.left = Math.min(x + 16, rect.width - 240) + "px";
    this._tip.style.top = Math.min(y + 14, rect.height - 70) + "px";
  };

  GraphView.prototype._hideTip = function () {
    if (this._tip) { this._tip.style.opacity = "0"; }
  };

  /* ------------------------------------------------------------------- datos */

  GraphView.prototype.setData = function (data, options) {
    if (!this._ready) { return; }
    var self = this;
    var merge = !!(options && options.merge) && this.nodes.length > 0;

    if (!merge) {
      this.cy.elements().remove();
      this.nodes = [];
      this.edges = [];
      this.pinned = [];
      this.selected = null;
      this.peek = [];
      this.rechazo = [];
    }

    var known = {};
    this.cy.nodes().forEach(function (node) { known[node.id()] = true; });

    var fresh = [];
    (data.nodes || []).forEach(function (raw) {
      if (!raw || !raw.id) { return; }
      var kind = raw.label || "Nodo";
      if (known[raw.id]) {
        var current = self.cy.getElementById(raw.id);
        if (current && current.length) {
          current.data("name", raw.name || raw.id);
          var item = null;
          for (var i = 0; i < self.nodes.length; i += 1) {
            if (self.nodes[i].id === raw.id) { item = self.nodes[i]; break; }
          }
          if (item) { item.name = raw.name || raw.id; item.data = raw; }
        }
        return;
      }
      known[raw.id] = true;
      self.nodes.push({ id: raw.id, label: kind, name: raw.name || raw.id, data: raw });
      fresh.push({
        group: "nodes",
        data: {
          id: raw.id,
          kind: kind,
          name: raw.name || raw.id,
          label: truncate(raw.name || raw.id, 22),
          color: self.colorFor(kind),
          colorHi: shade(self.colorFor(kind), 0.45),
          colorLo: shade(self.colorFor(kind), -0.55),
          colorEdge: shade(self.colorFor(kind), -0.35),
          shape: SHAPES[kind] || "ellipse",
          size: BASE_SIZE[kind] || 11,
          deg: 0
        }
      });
    });

    var edgeKeys = {};
    this.cy.edges().forEach(function (edge) {
      edgeKeys[edge.data("source") + "|" + edge.data("rel") + "|" + edge.data("target")] = true;
    });

    var freshEdges = [];
    (data.edges || []).forEach(function (raw) {
      if (!raw || !raw.source || !raw.target) { return; }
      if (!known[raw.source] || !known[raw.target]) { return; }
      var rel = raw.rel || "";
      var key = raw.source + "|" + rel + "|" + raw.target;
      if (edgeKeys[key]) { return; }
      edgeKeys[key] = true;
      freshEdges.push({
        group: "edges",
        data: { id: "e:" + key, source: raw.source, target: raw.target, rel: rel }
      });
      self.edges.push({ source: raw.source, target: raw.target, rel: rel });
    });

    if (fresh.length || freshEdges.length) {
      this.cy.add(fresh.concat(freshEdges));
    }

    //: El grado se lee del grafo ya armado, no de la respuesta: en un merge el vecindario nuevo
    //: cambia el grado de nodos que ya estaban, y con una foto vieja los hubs quedarían chicos.
    this.cy.nodes().forEach(function (node) {
      var degree = node.connectedEdges().length;
      var kind = node.data("kind");
      node.data("deg", degree);
      //: La escala del explorador de memoria: 7 px para una hoja, 26 px para un hub. Mi versión
      //: anterior llegaba a 72 px con grado 120, y a ese tamaño los nodos se tocan entre sí:
      //: el grafo se ve como una masa, no como una red.
      node.data("size", Math.round(7 + (Math.min(degree, 40) / 40) * 19));
    });

    //: Los ocho nodos más centrales llevan nombre siempre. Un umbral fijo de grado deja sin
    //: etiqueta al hub de un vecindario chico, que es justo el que hay que poder leer.
    this.cy.nodes().removeClass("lbl");
    this.cy.nodes().slice().sort(function (a, b) {
      return b.data("deg") - a.data("deg");
    }).slice(0, 8).forEach(function (node) { node.addClass("lbl"); });

    this._applyHidden();
    this._layout(!merge);

    if (data.seed) { this.selected = data.seed; }
    this._paint();
    if (this.options.legend) { this._renderLegend(); }
  };

  GraphView.prototype._layout = function (fresh) {
    var visible = this.cy.nodes().not(".oculto");

    /* `eles` NO se pasa, y esto no es un detalle.
     *
     * Con `eles: <colección>`, fcose deja los nodos en una grilla regular en vez de relajarlos:
     * el grafo se ve como un tablero de ajedrez y no como una red. Sin `eles`, el layout corre
     * sobre todo el grafo y después se encuadra solo lo visible. Los nodos fuera del foco igual
     * reciben posición —no se ven— y así el día que se muestren ya están ubicados.
     */
    var options = {
      name: global.cytoscapeFcose ? "fcose" : "cose",
      animate: false,
      fit: false,
      padding: 90,
      randomize: !!fresh,
      nodeRepulsion: 28000,
      idealEdgeLength: 150,
      edgeElasticity: 0.32,
      gravity: 0.20,
      numIter: fresh ? 2600 : 900,
      nodeSeparation: 200,
      quality: "default",
      samplingType: true,
      packComponents: true
    };

    try {
      this.cy.layout(options).run();
    } catch (error) {
      this.cy.layout({ name: "cose", animate: false, fit: false, padding: 90 }).run();
    }

    /* La cámara NO encuadra todo: encuadra el núcleo.
     *
     * El corpus entero vive en el lienzo (957 nodos, fuerza medida en 569-806 ms), pero verlo
     * todo de una vez devuelve la bola de pelo. Encuadrar el hub de mayor grado deja al lector
     * en una zona legible y el resto queda a un arrastre de distancia: el lienzo es grande, la
     * cámara es chica. "Ver todo" es una acción de cámara (§reset), no de visibilidad.
     */
    if (fresh) {
      this.centerOnCore(0);
    } else {
      this.cy.animate({ fit: { eles: visible, padding: 90 } }, { duration: 420 });
    }
    this.container.style.opacity = "1";
  };

  /* La cámara, que es lo único que decide qué se mira.
   *
   * No se esconde nada: los 957 nodos están en el lienzo y se llega a cualquiera arrastrando o
   * buscando. Lo que cambia es dónde está parada la cámara. Eso importa por una razón de
   * producto: un grafo recortado miente por omisión; una cámara movida no.
   */
  GraphView.prototype.centerOnCore = function (duration) {
    var core = this.cy.nodes().filter(function (node) {
      return node.data("kind") === "Detector";
    });
    if (!core.length) { core = this.cy.nodes().sort(function (a, b) { return b.data("deg") - a.data("deg"); }).slice(0, 3); }
    if (!core.length) { return false; }
    var target = core.sort(function (a, b) { return b.data("deg") - a.data("deg"); }).first();
    var zoom = 0.85;
    if (duration === 0) {
      this.cy.center(target);
      this.cy.zoom(zoom);
      return true;
    }
    this.cy.animate({ center: { eles: target }, zoom: zoom }, { duration: duration || 600 });
    return true;
  };

  //: "Ver todo" es un movimiento de cámara: el corpus no aparece ni desaparece, se encuadra.
  GraphView.prototype.showAll = function (duration) {
    //: Con duración 0, `animate` no aplica el encuadre: se hace con `fit`, que es inmediato.
    //: (El bug se vio como "el botón no hace nada": el zoom quedaba idéntico.)
    if (duration === 0) {
      this.cy.fit(this.cy.elements(), 40);
    } else {
      this.cy.animate(
        { fit: { eles: this.cy.elements(), padding: 40 } },
        { duration: duration === undefined ? 700 : duration }
      );
    }
    return this.cy.nodes().length;
  };

  /* Llevar la cámara a los nodos que un paso de la investigación tocó.
   *
   * No cambia la selección ni el panel: el registro manda sobre la cámara, no sobre el estado.
   * Si el nodo no está en el lienzo, devuelve false y el llamador decide (pedir el vecindario).
   */
  GraphView.prototype.follow = function (ids, zoom) {
    var list = ids || [];
    for (var i = 0; i < list.length; i += 1) {
      var node = this.cy.getElementById(String(list[i]));
      if (node && node.length) {
        var level = zoom || Math.max(0.75, Math.min(1.5, this.cy.zoom()));
        this.cy.animate({ center: { eles: node }, zoom: level }, { duration: 620, easing: "ease-in-out-cubic" });
        return true;
      }
    }
    return false;
  };

  /* --------------------------------------------------------------- pintado */

  GraphView.prototype._paint = function () {
    if (!this._ready) { return; }
    this.cy.elements().removeClass("sel pin route rejected peek pulse hl");
    if (this.selected) {
      var sel = this.cy.getElementById(this.selected);
      if (sel && sel.length) {
        sel.addClass("sel");
        sel.connectedEdges().addClass("hl");
      }
    }
    var self = this;
    this.pinned.forEach(function (id) {
      var node = self.cy.getElementById(id);
      if (node && node.length) {
        node.addClass("pin");
        node.connectedEdges().addClass("hl");
      }
    });
    if (this.peek.length) {
      var lit = this.cy.collection();
      this.peek.forEach(function (id) {
        var node = self.cy.getElementById(id);
        if (node && node.length) { lit = lit.union(node); }
      });
      lit.addClass("peek");
      this.cy.elements().not(lit).addClass("faded");
    }
    this.rechazo.forEach(function (id) {
      var node = self.cy.getElementById(id);
      if (node && node.length) { node.removeClass("faded").addClass("rejected"); }
    });
  };

  GraphView.prototype.render = function () { this._paint(); };

  /* ------------------------------------------------------------------ cámara */

  GraphView.prototype.flyTo = function (nodeId, zoom) {
    if (!this._ready) { return false; }
    var node = this.cy.getElementById(nodeId);
    if (!node || !node.length) { return false; }
    this.selected = nodeId;
    this._paint();
    node.addClass("pulse");
    global.setTimeout(function () { node.removeClass("pulse"); }, 1500);
    this.cy.animate(
      { center: { eles: node }, zoom: Math.max(0.4, Math.min(3.2, zoom || 1.8)) },
      { duration: 620, easing: "ease-in-out-cubic" }
    );
    return true;
  };

  GraphView.prototype.zoomBy = function (factor) {
    if (!this._ready) { return; }
    var next = this.cy.zoom() * (factor || 1);
    next = Math.max(0.08, Math.min(3.4, next));
    this.cy.animate({ zoom: next }, { duration: 180 });
  };

  GraphView.prototype.frameNodes = function (ids, padding) {
    if (!this._ready) { return false; }
    var collection = this.cy.collection();
    (ids || []).forEach(function (id) {
      var node = this.cy.getElementById(id);
      if (node && node.length) { collection = collection.union(node); }
    }, this);
    if (!collection.length) { return false; }
    this.cy.animate(
      { fit: { eles: collection, padding: Math.max(40, (padding || 1) * 60) } },
      { duration: 460 }
    );
    return true;
  };

  GraphView.prototype.reset = function () {
    if (!this._ready) { return; }
    this.hidden = {};
    this.solo = null;
    this.pinned = [];
    this.selected = null;
    this.peek = [];
    this.rechazo = [];
    this.cy.elements().removeClass("oculto faded sel pin route rejected peek");
    this.showAll();
    if (this.options.legend) { this._renderLegend(); }
  };

  /* ------------------------------------------------------------- interacción */

  GraphView.prototype.pin = function (nodeId) {
    var at = this.pinned.indexOf(nodeId);
    if (at >= 0) {
      this.pinned.splice(at, 1);
    } else {
      this.pinned.push(nodeId);
      while (this.pinned.length > 2) { this.pinned.shift(); }
    }
    this._paint();
    return this.pinned.slice();
  };

  GraphView.prototype.clearPins = function () {
    this.pinned = [];
    this._paint();
  };

  GraphView.prototype.setPeek = function (ids) {
    this.peek = (ids || []).slice();
    this._paint();
    return this.peek.length;
  };

  /* Rebobinado: pinta lo que la grabación dice que había ocurrido hasta ese punto.
   *
   * No vuelve a ejecutar nada. Relee eventos que ya están en memoria y los re-aplica a la vista
   * —sin modelo, sin red, sin base de datos—, igual que el rebobinado del expediente.
   * `rechazados` son los nodos cuya afirmación fue borrada por ese momento: van en rojo.
   */
  GraphView.prototype.setReplay = function (ids, rechazados) {
    this.peek = (ids || []).slice();
    this.rechazo = (rechazados || []).slice();
    this._paint();
    return { vivos: this.peek.length, rechazados: this.rechazo.length };
  };

  /* Rastro del recorrido en el mapa: los mismos nodos que el 3D encendió.
   *
   * Se guarda la secuencia completa y se redibuja entera en cada paso: la ruta tiene que
   * crecer, no reemplazarse. `showPath` limpia y vuelve a marcar, así que recibe el acumulado.
   */
  GraphView.prototype.markTrail = function (id, prevId) {
    if (!this._trail) { this._trail = []; }
    if (prevId && !this._trail.length) { this._trail.push(prevId); }
    if (this._trail.indexOf(id) < 0) { this._trail.push(id); }
    this.showPath(this._trail.slice());
  };

  GraphView.prototype.clearTrail = function () {
    this._trail = [];
    this.cy.elements().removeClass("route");
  };

  GraphView.prototype.showPath = function (ids) {
    if (!this._ready) { return; }
    var self = this;
    this.cy.elements().removeClass("route");
    var previous = null;
    (ids || []).forEach(function (id) {
      var node = self.cy.getElementById(id);
      if (!node || !node.length) { return; }
      node.addClass("route");
      if (previous) {
        var step = node.edgesTo(previous).union(previous.edgesTo(node));
        if (step.length) { step.addClass("route"); }
        else {
          //: el tramo existe en la ruta aunque falte la arista explícita: se dibuja una línea
          //: de referencia para que el camino no se corte en el medio.
          step = node.edgesWith(previous);
          if (step.length) { step.addClass("route"); }
        }
      }
      previous = node;
    });
  };

  /* ------------------------------------------------------------------ capas */

  GraphView.prototype.setLabelVisible = function (label, visible) {
    this.hidden[label] = !visible;
    this._applyHidden();
    if (this.options.legend) { this._renderLegend(); }
    return !this.hidden[label];
  };

  GraphView.prototype.isHidden = function (label) {
    if (this.solo && label !== this.solo) { return true; }
    return !!this.hidden[label];
  };

  GraphView.prototype.setSolo = function (label) {
    this.solo = label || null;
    this._applyHidden();
    return this.solo;
  };

  GraphView.prototype._applyHidden = function () {
    if (!this._ready) { return; }
    var self = this;
    this.cy.nodes().forEach(function (node) {
      if (self.isHidden(node.data("kind"))) { node.addClass("oculto"); }
      else { node.removeClass("oculto"); }
    });
    this.cy.edges().forEach(function (edge) {
      var source = edge.source();
      var target = edge.target();
      if (source.hasClass("oculto") || target.hasClass("oculto")) { edge.addClass("oculto"); }
      else { edge.removeClass("oculto"); }
    });
  };

  GraphView.prototype.counts = function () {
    var tally = {};
    this.cy.nodes().forEach(function (node) {
      var kind = node.data("kind");
      tally[kind] = (tally[kind] || 0) + 1;
    });
    return tally;
  };

  GraphView.prototype.relations = function () {
    var tally = {};
    this.cy.edges().forEach(function (edge) {
      var rel = edge.data("rel");
      tally[rel] = (tally[rel] || 0) + 1;
    });
    return Object.keys(tally).sort(function (a, b) { return tally[b] - tally[a]; })
      .map(function (rel) {
        return { rel: rel, count: tally[rel], meaning: REL_ES[rel] || rel };
      });
  };

  GraphView.prototype.labels = function () {
    var seen = {};
    return this.cy.nodes().filter(function (node) {
      var kind = node.data("kind");
      if (seen[kind]) { return false; }
      seen[kind] = true;
      return true;
    }).map(function (node) {
      return { label: node.data("kind"), color: this.colorFor(node.data("kind")) };
    }, this);
  };

  //: Compatibilidad: la segunda cámara se fue, pero `mode` sigue existiendo porque el registro y
  //: el guion del demo lo consultan. Devolver "2d" siempre es la verdad, no un parche.
  GraphView.prototype.setMode = function () { return "2d"; };
  GraphView.prototype.toggleMode = function () { return "2d"; };

  /* ------------------------------------------------------------------ leyenda */

  GraphView.prototype._mountLegend = function () {
    this._legend = document.createElement("div");
    this._legend.className = "leyenda-grafo";
    this._legend.setAttribute("aria-label", "Tipos de nodo");
    this.container.appendChild(this._legend);
  };

  GraphView.prototype._renderLegend = function () {
    if (!this._legend) { return; }
    var self = this;
    var counts = this.counts();
    var kinds = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    this._legend.innerHTML = "";

    var title = document.createElement("p");
    title.className = "leyenda-titulo";
    title.textContent = "tipos";
    this._legend.appendChild(title);

    kinds.forEach(function (kind) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "leyenda-fila" + (self.isHidden(kind) ? " apagada" : "");
      row.title = "Mostrar u ocultar " + (LABELS_ES[kind] || kind);
      var dot = document.createElement("i");
      dot.style.background = self.colorFor(kind);
      dot.style.boxShadow = "0 0 9px " + self.colorFor(kind);
      var name = document.createElement("span");
      name.textContent = LABELS_ES[kind] || kind;
      var total = document.createElement("b");
      total.textContent = String(counts[kind]);
      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(total);
      row.addEventListener("click", function () {
        var visible = self.isHidden(kind);
        self.setLabelVisible(kind, visible);
        if (global.expedienteActualizarCapas) { global.expedienteActualizarCapas(); }
      });
      self._legend.appendChild(row);
    });

    //: El pie dice cuántos hay en el lienzo y ofrece las dos cámaras. No declara un recorte
    //: porque no hay recorte: los 957 están dibujados, lo que se elige es dónde mirar.
    var pie = document.createElement("p");
    pie.className = "leyenda-pie-texto";
    var huerfanas = this.cy.nodes().filter(function (node) {
      return node.data("kind") === "Senal" && node.data("deg") === 1;
    }).length;
    pie.textContent = this.cy.nodes().length + " nodos · "
      + (huerfanas ? huerfanas + " señales sin órdenes cargadas (atenuadas)" : "todas con evidencia");
    this._legend.appendChild(pie);

    var volver = document.createElement("button");
    volver.type = "button";
    volver.className = "leyenda-pie";
    volver.textContent = "volver al núcleo";
    volver.addEventListener("click", function () { self.centerOnCore(700); });
    this._legend.appendChild(volver);

    var todo = document.createElement("button");
    todo.type = "button";
    todo.className = "leyenda-pie";
    todo.textContent = "encuadrar todo";
    todo.addEventListener("click", function () { self.showAll(); });
    this._legend.appendChild(todo);
  };

  /* Una foto de lo que hay en el lienzo, con los campos crudos del servidor incluidos.
   *
   * La cámara 3D dibuja el mismo grafo, no una copia distinta: si el foco deja algo afuera,
   * la foto también lo deja afuera. Una segunda vista que muestra más que la primera es una
   * segunda verdad.
   */
  GraphView.prototype.snapshot = function () {
    var self = this;
    var visible = {};
    this.cy.nodes().forEach(function (node) {
      visible[node.id()] = !node.hasClass("oculto");
    });
    return {
      nodes: this.nodes.filter(function (node) { return visible[node.id]; }).map(function (node) {
        return {
          id: node.id,
          kind: node.label,
          name: node.name,
          color: self.colorFor(node.label),
          size: self.cy.getElementById(node.id).data("size") || 12,
          deg: self.cy.getElementById(node.id).data("deg") || 1
        };
      }),
      edges: this.edges.filter(function (edge) {
        return visible[edge.source] && visible[edge.target];
      }).map(function (edge) {
        return { source: edge.source, target: edge.target, rel: edge.rel };
      })
    };
  };

  global.GraphView = GraphView;
  global.GraphRelNames = REL_ES;
  global.GraphLabelsES = LABELS_ES;
})(window);
