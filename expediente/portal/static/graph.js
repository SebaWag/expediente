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
  var SHAPES = {
    Detector: "hexagon",
    Senal: "diamond",
    OrdenCompra: "ellipse",
    Proveedor: "ellipse",
    Organismo: "round-rectangle",
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

  //: El landing llega con el corpus muestreado (hoy ~957 nodos, 565 de ellos señales). Dibujar
  //: eso de una vez es una bola de pelo: el ojo no aterriza en ninguna parte. Se abre en foco
  //: —la maquinaria, las entidades estructurales y las señales más conectadas— y el resto queda
  //: a un clic, declarado en pantalla. Es la misma decisión del explorador de memoria: arrancar
  //: en la red ego, no en el grafo completo.
  var FOCUS_LIMIT = 130;

  //: Prioridad del foco por tipo. La maquinaria primero, el volumen al final.
  var FOCUS_RANK = {
    Detector: 0, Organismo: 1, Proveedor: 1, Persona: 2, Domicilio: 2,
    Senal: 3, Documento: 4, Evidencia: 4, Afirmacion: 4, Licitacion: 4,
    CompraAgil: 4, OrdenCompra: 5
  };

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
    this._showAll = false;     //: el corpus completo se pide, no se impone
    this._total = 0;
    this._focused = 0;
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
          "border-width": 1.4,
          "border-color": "data(colorEdge)",
          "border-opacity": 0.75,
          //: El halo: cytoscape no tiene box-shadow, pero overlay es exactamente un halo del
          //: color del nodo. Con opacidad baja se lee como brillo, no como borde grueso.
          "overlay-color": "data(color)",
          "overlay-opacity": 0.11,
          "overlay-padding": 8,
          "label": "data(label)",
          "font-size": 12,
          "font-family": "system-ui, sans-serif",
          "font-weight": 500,
          "color": "#e2e8f0",
          "text-opacity": 0,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 7,
          "text-outline-width": 3.4,
          "text-outline-color": "#050914",
          "text-outline-opacity": 0.95,
          //: La pastilla es el ancla: sin fondo, una etiqueta en un campo oscuro flota y el ojo
          //: no sabe a qué nodo pertenece. Con fondo, es una etiqueta pegada a su nodo.
          "text-background-color": "#16233b",
          "text-background-opacity": 0.9,
          "text-background-padding": 3,
          "text-background-shape": "roundrectangle",
          "text-max-width": "150px",
          "text-wrap": "ellipsis",
          "min-zoomed-font-size": 9,
          "transition-property": "opacity, border-color, border-width, background-color",
          "transition-duration": "150ms"
        } },
        //: Solo los hubs llevan etiqueta permanente. Con 70 nombres encendidos no se lee ninguno.
        { selector: "node[deg >= 7]", style: { "text-opacity": 1 } },
        { selector: "node.lbl", style: { "text-opacity": 1 } },
        { selector: 'node[kind = "OrdenCompra"]', style: {
          "background-opacity": 0.55, "overlay-opacity": 0.05, "border-opacity": 0.55
        } },
        { selector: "edge", style: {
          "width": 1.1,
          "line-color": "rgba(148,163,184,0.26)",
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
          "border-width": 3.2, "border-color": "#ffffff", "text-opacity": 1, opacity: 1,
          "overlay-opacity": 0.24
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
        //: `fuera` es el recorte del foco, y es independiente del filtro por tipo: apagar una
        //: capa no debe re-dibujar el foco, ni al revés.
        { selector: "node.fuera", style: { display: "none" } },
        { selector: "edge.oculto", style: { display: "none" } }
      ]
    });

    //: El degradado radial convierte el círculo en esfera. Cytoscape no acepta `data()` en los
    //: stops de un gradiente, así que las doce reglas se escriben con el color literal de la
    //: paleta: son doce y no cambian en tiempo de ejecución.
    var self = this;
    Object.keys(COLORS).forEach(function (kind) {
      var base = COLORS[kind];
      self.cy.style()
        .selector('node[kind = "' + kind + '"]')
        .style({
          "background-fill": "radial-gradient",
          "background-gradient-stop-colors":
            shade(base, 0.42) + " " + base + " " + shade(base, -0.58),
          "background-gradient-stop-positions": "0% 46% 100%",
          "border-color": shade(base, -0.30)
        })
        .update();
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
      var base = BASE_SIZE[kind] || 11;
      node.data("deg", degree);
      node.data("size", base + Math.min(26, Math.sqrt(degree) * 4.2));
    });

    //: Los ocho nodos más centrales llevan nombre siempre. Un umbral fijo de grado deja sin
    //: etiqueta al hub de un vecindario chico, que es justo el que hay que poder leer.
    this.cy.nodes().removeClass("lbl");
    this.cy.nodes().slice().sort(function (a, b) {
      return b.data("deg") - a.data("deg");
    }).slice(0, 8).forEach(function (node) { node.addClass("lbl"); });

    this._applyHidden();
    if (!merge) { this._applyFocus(); }
    this._layout(!merge);

    if (data.seed) { this.selected = data.seed; }
    this._paint();
    if (this.options.legend) { this._renderLegend(); }
  };

  GraphView.prototype._layout = function (fresh) {
    var visible = this.cy.nodes().not(".fuera, .oculto");

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

    //: El corpus completo no se dibuja con force-directed: a ese tamaño el resultado es una
    //: bola. Concéntrico por grado dice "quién manda", que es la única pregunta que un mapa
    //: de mil nodos puede contestar de un vistazo.
    if (visible.length > 420) {
      options = {
        name: "concentric",
        animate: false,
        fit: false,
        padding: 70,
        concentric: function (node) { return node.data("deg"); },
        levelWidth: function (nodes) { return Math.max(2, Math.round(nodes.length / 12)); },
        minNodeSpacing: 26
      };
    }

    try {
      this.cy.layout(options).run();
    } catch (error) {
      this.cy.layout({ name: "cose", animate: false, fit: false, padding: 90 }).run();
    }

    //: El encuadre sí es sobre lo visible: si se encuadra todo, el foco queda en una esquina.
    if (fresh) {
      this.cy.fit(visible, 90);
    } else {
      this.cy.animate({ fit: { eles: visible, padding: 90 } }, { duration: 420 });
    }
    this.container.style.opacity = "1";
  };

  /* El foco: qué se dibuja del corpus, y cuánto se declara que se dejó afuera.
   *
   * No se borra nada. Los nodos fuera del foco siguen en el grafo y siguen siendo alcanzables
   * por búsqueda; solo no se dibujan hasta que alguien pide el todo. Un lienzo que dice
   * "130 de 957" es honesto; uno que dibuja 130 y parece completo, no.
   */
  GraphView.prototype._applyFocus = function () {
    var nodes = this.cy.nodes();
    nodes.removeClass("fuera");
    this._total = nodes.length;
    if (this._showAll || nodes.length <= FOCUS_LIMIT) {
      this._focused = nodes.length;
      return;
    }
    var ranked = nodes.slice().sort(function (a, b) {
      var ra = FOCUS_RANK[a.data("kind")];
      var rb = FOCUS_RANK[b.data("kind")];
      if (ra === undefined) { ra = 4; }
      if (rb === undefined) { rb = 4; }
      if (ra !== rb) { return ra - rb; }
      return b.data("deg") - a.data("deg");
    });
    ranked.slice(FOCUS_LIMIT).forEach(function (node) { node.addClass("fuera"); });
    this._focused = FOCUS_LIMIT;
  };

  //: "Ver todo" y "volver al foco". Es la misma decisión del explorador de memoria: el grafo
  //: completo es el remate, no la entrada.
  GraphView.prototype.toggleFocus = function (showAll) {
    this._showAll = (showAll === undefined) ? !this._showAll : !!showAll;
    this._applyFocus();
    this._layout(false);
    if (this.options.legend) { this._renderLegend(); }
    return this._showAll;
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
    this.cy.animate({ fit: { eles: this.cy.elements(), padding: 90 } }, { duration: 420 });
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
    this.cy.nodes().not(".fuera").forEach(function (node) {
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

    //: El pie declara el recorte y ofrece el todo. Es el mismo pacto del aviso del servidor:
    //: el lienzo dice cuánto está mostrando, siempre.
    var foot = document.createElement("button");
    foot.type = "button";
    foot.className = "leyenda-pie";
    if (this._showAll || this._total <= FOCUS_LIMIT) {
      foot.textContent = "mostrando las " + this._total + " · volver al foco";
      foot.addEventListener("click", function () { self.toggleFocus(false); });
    } else {
      foot.textContent = "mostrando " + this._focused + " de " + this._total + " · ver todo";
      foot.addEventListener("click", function () { self.toggleFocus(true); });
    }
    this._legend.appendChild(foot);
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
      visible[node.id()] = !node.hasClass("fuera") && !node.hasClass("oculto");
    });
    return {
      nodes: this.nodes.filter(function (node) { return visible[node.id]; }).map(function (node) {
        return {
          id: node.id,
          kind: node.label,
          name: node.name,
          color: self.colorFor(node.label),
          size: self.cy.getElementById(node.id).data("size") || 12
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
