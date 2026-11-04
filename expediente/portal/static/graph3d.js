/* La cámara 3D: el mismo grafo, visto en volumen.
 *
 * ## Por qué existe, si ya hay un mapa
 *
 * El mapa 2D contesta "dónde está esto respecto de aquello". No contesta "qué forma tiene", porque
 * en un plano toda relación vecina se cruza con otra. Girar el grafo es la única manera de
 * entender que un proveedor no está *al lado* de un domicilio: está *encima*, y esa distancia es
 * una tercera dimensión que el plano aplasta.
 *
 * ## La regla que se conserva
 *
 * Se dibuja **la misma foto** que el mapa (`GraphView.snapshot`): los mismos nodos, las mismas
 * aristas, el mismo recorte declarado. Si el foco dejó 800 nodos afuera en 2D, acá tampoco
 * aparecen. Una segunda vista que muestra más que la primera es una segunda verdad, y este
 * producto vive de no tener dos.
 *
 * ## Lo que el 3D no hace
 *
 * No decide nada. No es una animación decorativa: no hay partículas ni pulsos inventados. Los
 * nodos son esferas del color de su tipo y del tamaño de su grado; las aristas, líneas. Lo único
 * que se mueve es la cámara, porque la mueve la persona.
 */
(function (global) {
  "use strict";

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

  function Graph3D(host, options) {
    this.host = host;
    this.options = options || {};
    this.visible = false;
    this._built = false;
    this._graph = null;
    this._data = { nodes: [], links: [] };
    this._selected = null;
    this._labels = null;      //: capa HTML con los nombres
    this._labelled = [];      //: los nodos que llevan nombre permanente
    this._loop = null;
  }

  Graph3D.prototype._build = function () {
    var self = this;
    if (this._built || !global.ForceGraph3D) {
      if (!global.ForceGraph3D && global.console) {
        global.console.error("Expediente: falta vendor/3d-force-graph.min.js");
      }
      return;
    }
    this.host.innerHTML = "";

    this._graph = global.ForceGraph3D({ controlType: "orbit" })(this.host)
      .backgroundColor("rgba(0,0,0,0)")
      .showNavInfo(false)
      //: Una esfera de 12 caras se lee como un disco. La resolución es lo que separa "nodo 3D"
      //: de "calcomanía vista de lado".
      .nodeResolution(14)
      //: Los valores son los del explorador de la memoria, que es la referencia: `nodeRelSize`
      //: 7 y `val = 0.6 + min(deg,45)*0.36`. Con relSize 4 y una val cúbica las esferas quedaban
      //: 3x más chicas y a la distancia de encuadre se veían como puntos planos.
      .nodeRelSize(7)
      .nodeVal(function (node) { return 0.6 + Math.min(node.deg || 1, 45) * 0.36; })
      .nodeColor(function (node) { return node.color; })
      .nodeOpacity(0.92)
      .nodeLabel(function (node) {
        var kind = LABELS_ES[node.kind] || node.kind;
        return '<div class="tip3d"><b>' + escapeHtml(node.name) + "</b><span>" + kind + "</span></div>";
      })
      .linkColor(function () { return "rgba(148,163,184,0.34)"; })
      .linkWidth(0.5)
      .linkOpacity(0.55)
      .linkDirectionalArrowLength(0)
      .d3AlphaDecay(0.024)
      .d3VelocityDecay(0.32)
      .warmupTicks(60)
      .cooldownTicks(240)
      .onNodeClick(function (node) {
        self._selected = node.id;
        if (self.options.onSelect) {
          self.options.onSelect({ id: node.id, name: node.name, label: node.kind });
        }
      })
      .onNodeRightClick(function (node) {
        if (self.options.onExpand) {
          self.options.onExpand({ id: node.id, name: node.name, label: node.kind });
        }
      });

    /* Fuerzas: las mismas del explorador de la memoria.
     *
     * Una carga de -30 (la del motor) deja el volumen ralo: las esferas quedan lejos entre sí y
     * el encuadre las achica hasta que se ven como puntos. Con -46 y una distancia de enlace de
     * 30 unidades, el grafo se aprieta como una red neuronal y cada esfera conserva su tamaño.
     */
    if (this._graph.d3Force("charge")) { this._graph.d3Force("charge").strength(-46); }
    if (this._graph.d3Force("link")) {
      this._graph.d3Force("link").distance(30).strength(0.5);
    }
    var centrar = this._graph.d3Force("center");
    if (centrar && typeof centrar.strength === "function") { centrar.strength(0.05); }

    /* Luz: el motor trae una ambiental y una direccional, y por defecto son tímidas.
     *
     * Sin subirlas, la esfera se ve plana porque la cara iluminada y la sombría tienen casi el
     * mismo valor. Subirlas es lo que hace que el nodo tenga un lado claro y un lado oscuro —
     * es decir, que tenga volumen. Aprendido del explorador de la memoria, donde ya funcionaba.
     */
    this._graph.scene().children.forEach(function (child) {
      if (child.isLight) {
        child.intensity = child.isAmbientLight ? 3.4 : 1.6;
      }
    });

    //: Amortiguación en los controles: girar se siente con peso, no a saltos.
    var controls = this._graph.controls();
    if (controls) {
      controls.enableDamping = true;
      controls.dampingFactor = 0.09;
      controls.rotateSpeed = 0.9;
      controls.zoomSpeed = 0.9;
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.32;
    }

    //: Etiquetas: una capa HTML encima del lienzo, reposicionada en cada cuadro. Se hace así
    //: —y no con sprites— porque el texto queda nítido a cualquier zoom y hereda la tipografía
    //: de la página. El motor no expone su Three.js, así que un sprite nos obligaría a cargar
    //: otra copia de la librería.
    this._labels = document.createElement("div");
    this._labels.className = "etiquetas-3d";
    this.host.appendChild(this._labels);

    //: La cámara arranca en tres cuartos, no de frente: de frente, un volumen se lee como plano.
    this._graph.cameraPosition({ x: 320, y: 380, z: 560 }, { x: 0, y: 0, z: 0 }, 0);
    this._built = true;
  };

  function escapeHtml(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* Se le pasa la foto del mapa, no la respuesta cruda del servidor.
   *
   * `fit` encuadra después de cargar: sin eso, un vecindario de nueve nodos queda del tamaño de
   * una pulga en el centro de la pantalla.
   */
  Graph3D.prototype.setData = function (snapshot) {
    this._build();
    if (!this._built) { return; }
    var nodes = (snapshot.nodes || []).map(function (node) {
      return { id: node.id, name: node.name, kind: node.kind, color: node.color, size: node.size };
    });
    var links = (snapshot.edges || []).map(function (edge) {
      return { source: edge.source, target: edge.target, rel: edge.rel };
    });
    this._data = { nodes: nodes, links: links };
    this._graph.graphData(this._data);

    //: Los ocho nodos más grandes llevan nombre siempre. El resto, al pasar el mouse: ocho
    //: etiquetas flotando en un volumen se tapan entre sí en cuanto la cámara gira.
    this._labelled = nodes.slice().sort(function (a, b) {
      return (b.size || 0) - (a.size || 0);
    }).slice(0, 8);
    this._buildLabels();
    //: Redimensionar un lienzo oculto lo deja en 0×0 y el motor revienta en el primer tick.
    //: El tamaño se ajusta cuando la cámara se muestra, que es cuando existe.
    if (this.visible) { this.resize(); }
  };

  Graph3D.prototype._buildLabels = function () {
    if (!this._labels) { return; }
    this._labels.innerHTML = "";
    var self = this;
    this._labelled.forEach(function (node) {
      var el = document.createElement("span");
      el.className = "etiqueta-3d";
      el.textContent = node.name;
      el.style.color = node.color;
      el.dataset.node = node.id;
      self._labels.appendChild(el);
    });
  };

  /* Reposicionar las etiquetas cada cuadro.
   *
   * `graph2ScreenCoords` proyecta el punto 3D al plano de la pantalla usando la cámara actual,
   * así que al girar las etiquetas siguen a su nodo. Si el nodo queda detrás de la cámara, la
   * proyección da coordenadas absurdas: se oculta en vez de dibujarlo en una esquina.
   */
  Graph3D.prototype._tickLabels = function () {
    if (!this._labels || !this._graph || !this.visible) { return; }
    var width = this.host.clientWidth || 1;
    var height = this.host.clientHeight || 1;
    for (var i = 0; i < this._labels.children.length; i += 1) {
      var el = this._labels.children[i];
      var node = null;
      for (var j = 0; j < this._data.nodes.length; j += 1) {
        if (this._data.nodes[j].id === el.dataset.node) { node = this._data.nodes[j]; break; }
      }
      if (!node || node.x === undefined) { el.style.opacity = "0"; continue; }
      var point = this._graph.graph2ScreenCoords(node.x, node.y, node.z);
      var fuera = point.x < 0 || point.x > width || point.y < 0 || point.y > height;
      el.style.opacity = fuera ? "0" : "1";
      el.style.transform = "translate(-50%, -50%) translate(" + point.x + "px," + point.y + "px)";
    }
  };

  Graph3D.prototype._startLoop = function () {
    var self = this;
    if (this._loop) { return; }
    var step = function () {
      self._tickLabels();
      self._loop = global.requestAnimationFrame(step);
    };
    this._loop = global.requestAnimationFrame(step);
  };

  Graph3D.prototype._stopLoop = function () {
    if (this._loop) {
      global.cancelAnimationFrame(this._loop);
      this._loop = null;
    }
  };

  Graph3D.prototype.show = function () {
    this._build();
    if (!this._built) { return false; }
    this.visible = true;
    this.host.hidden = false;
    this.resize();
    //: El motor se reanuda al mostrarse. Si no, el volumen se congela donde quedó y un grafo
    //: quieto con la cámara libre parece un render, no una herramienta.
    this._graph.resumeAnimation();
    //: `d3ReheatSimulation()` NO se llama: en esta versión lanza dentro del tick y mata el
    //: render (lo dejé documentado en el explorador de la memoria). Reenviar los datos reinicia
    //: el motor de forma segura.
    this._graph.graphData(this._data);
    this.fit();
    this._startLoop();
    return true;
  };

  Graph3D.prototype.hide = function () {
    this.visible = false;
    this.host.hidden = true;
    this._stopLoop();
    if (this._graph) { this._graph.pauseAnimation(); }
  };

  Graph3D.prototype.resize = function () {
    if (!this._graph) { return; }
    var width = this.host.clientWidth;
    var height = this.host.clientHeight;
    if (!width || !height) { return; }
    this._graph.width(width).height(height);
  };

  Graph3D.prototype.fit = function (duration) {
    if (!this._graph) { return; }
    this._graph.zoomToFit(duration === undefined ? 900 : duration, 24);
  };

  Graph3D.prototype.flyTo = function (nodeId) {
    if (!this._graph) { return false; }
    var node = null;
    for (var i = 0; i < this._data.nodes.length; i += 1) {
      if (this._data.nodes[i].id === nodeId) { node = this._data.nodes[i]; break; }
    }
    if (!node) { return false; }
    var distance = 160;
    var ratio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
    this._graph.cameraPosition(
      { x: (node.x || 0) * ratio, y: (node.y || 0) * ratio, z: (node.z || 0) * ratio },
      node,
      900
    );
    return true;
  };

  global.Graph3D = Graph3D;
})(window);
