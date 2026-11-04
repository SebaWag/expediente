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

  //: Hay un recorrido en curso. Los accessors de color la consultan para atenuar el resto.
  var HAY_RASTRO = false;
  function senalDeRastro() { return HAY_RASTRO; }

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
    this._hidden = {};        //: tipos apagados desde el panel de capas
    this._trail = [];         //: ids en el orden en que el agente los tocó
    this._trailLinks = {};    //: "origen|destino" de los saltos recorridos
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
      //: El nodo visitado crece: en un volumen de 957 esferas, el tamaño es la única marca que
      //: se lee sin buscar. El aclarado solo no alcanzaba (medido: no se distinguía el camino).
      .nodeVal(function (node) {
        var base = 0.6 + Math.min(node.deg || 1, 45) * 0.36;
        return node.visitado ? base * 2.4 : base;
      })
      //: Un nodo visitado se enciende; el resto queda igual. Es el recorrido de la investigación
      //: lo que se dibuja, y solo eso: no hay destellos decorativos.
      //: El filtro por tipo del panel de capas. Sin esto, apagar "órdenes" no hacía nada en 3D.
      .nodeVisibility(function (node) { return !this._hidden[node.kind]; }.bind(this))
      .linkVisibility(function (link) {
        var a = typeof link.source === "object" ? link.source.kind : null;
        var b = typeof link.target === "object" ? link.target.kind : null;
        if (a && this._hidden[a]) { return false; }
        if (b && this._hidden[b]) { return false; }
        return true;
      }.bind(this))
      .nodeColor(function (node) {
        if (node.visitado) { return aclarar(node.color, 0.55); }
        //: Señal sin órdenes cargadas: existe, pero no hay nada que investigar en ella todavía.
        if (node.kind === "Senal" && (node.deg || 1) === 1) { return atenuar(node.color, 0.84); }
        //: Con un recorrido en curso, el resto baja de intensidad: el camino se lee primero.
        return senalDeRastro() ? atenuar(node.color, 0.55) : node.color;
      })
      .nodeOpacity(0.92)
      .nodeLabel(function (node) {
        var kind = LABELS_ES[node.kind] || node.kind;
        return '<div class="tip3d"><b>' + escapeHtml(node.name) + "</b><span>" + kind + "</span></div>";
      })
      .linkColor(function (link) {
        if (link.recorrido) { return "#7dd3fc"; }
        return senalDeRastro() ? "rgba(148,163,184,0.10)" : "rgba(148,163,184,0.34)";
      })
      .linkWidth(function (link) { return link.recorrido ? 3 : 0.5; })
      .linkOpacity(0.55)
      //: La luz que viaja por la arista es lo que hace visible el "paso a paso": el jurado ve
      //: por dónde va el agente, no solo dónde llegó.
      .linkDirectionalParticles(function (link) { return link.recorrido ? 6 : 0; })
      .linkDirectionalParticleWidth(3.2)
      .linkDirectionalParticleSpeed(0.014)
      .linkDirectionalParticleColor(function () { return "#7dd3fc"; })
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

  //: Oscurece un color. Se usa para retirar del foco lo que no es parte del recorrido.
  function atenuar(hex, amount) {
    var v = String(hex).replace("#", "");
    if (v.length === 3) { v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2]; }
    var n = parseInt(v, 16);
    var canales = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (c) {
      return Math.round(c * (1 - amount));
    });
    return "#" + canales.map(function (c) { return ("0" + c.toString(16)).slice(-2); }).join("");
  }

  //: Aclara un color para marcar lo visitado. Se calcula acá y no en el CSS porque las esferas
  //: son materiales de Three, no DOM.
  function aclarar(hex, amount) {
    var v = String(hex).replace("#", "");
    if (v.length === 3) { v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2]; }
    var n = parseInt(v, 16);
    var canales = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (c) {
      return Math.round(c + (255 - c) * amount);
    });
    return "#" + canales.map(function (c) { return ("0" + c.toString(16)).slice(-2); }).join("");
  }

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

    //: Los cinco nodos más grandes llevan nombre. Con 1.437 esferas, ocho etiquetas se pisaban
    //: entre sí y tapaban justo la estructura que hay que ver. El resto, al pasar el mouse: ocho
    //: etiquetas flotando en un volumen se tapan entre sí en cuanto la cámara gira.
    this._labelled = nodes.slice().sort(function (a, b) {
      return (b.size || 0) - (a.size || 0);
    }).slice(0, 5);
    this._buildLabels();
    //: Redimensionar un lienzo oculto lo deja en 0×0 y el motor revienta en el primer tick.
    //: El tamaño se ajusta cuando la cámara se muestra, que es cuando existe.
    if (this.visible) { this.resize(); }
  };

  /* Marcar un paso del recorrido.
   *
   * `prevId` es el nodo del paso anterior: con él se enciende la arista que el agente cruzó.
   * Sin ese dato el recorrido sería una lista de nodos encendidos, no un camino.
   */
  Graph3D.prototype.markTrail = function (id, prevId) {
    if (!this._graph || !id) { return; }
    if (this._trail.indexOf(id) < 0) { this._trail.push(id); }
    for (var i = 0; i < this._data.nodes.length; i += 1) {
      if (this._data.nodes[i].id === id) { this._data.nodes[i].visitado = true; }
    }
    if (prevId) {
      var clave = prevId + "|" + id;
      this._trailLinks[clave] = true;
      this._data.links.forEach(function (link) {
        var a = typeof link.source === "object" ? link.source.id : link.source;
        var b = typeof link.target === "object" ? link.target.id : link.target;
        if ((a + "|" + b) === clave || (b + "|" + a) === clave) { link.recorrido = true; }
      });
    }
    //: `refresh()` re-evalúa los accessors sin reiniciar la simulación: el volumen no salta.
    HAY_RASTRO = true;
    this._buildLabels();
    if (this._graph.refresh) { this._graph.refresh(); }
  };

  Graph3D.prototype.clearTrail = function () {
    this._trail = [];
    this._trailLinks = {};
    this._data.nodes.forEach(function (node) { node.visitado = false; });
    this._data.links.forEach(function (link) { link.recorrido = false; });
    HAY_RASTRO = false;
    this._buildLabels();
    if (this._graph && this._graph.refresh) { this._graph.refresh(); }
  };

  //: ①..⑳: el número es el orden del recorrido. Sin él, cuatro esferas encendidas son cuatro
  //: esferas; con él, son un camino que se puede narrar en voz alta.
  var NUMEROS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳".split("");

  Graph3D.prototype._buildLabels = function () {
    if (!this._labels) { return; }
    this._labels.innerHTML = "";
    var self = this;
    var visitados = this._data.nodes.filter(function (n) { return n.visitado; });

    var etiquetas = visitados.map(function (node, i) {
      return { node: node, badge: NUMEROS[i] || (i + 1) + "·" };
    }).concat(
      this._labelled.filter(function (node) { return !node.visitado; })
        .map(function (node) { return { node: node, badge: "" }; })
    );

    etiquetas.forEach(function (item) {
      var el = document.createElement("span");
      el.className = "etiqueta-3d" + (item.badge ? " en-ruta" : "");
      el.textContent = item.badge ? item.badge + " " + item.node.name : item.node.name;
      el.style.color = item.badge ? "#e8f6ff" : item.node.color;
      el.dataset.node = item.node.id;
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

    //: Lejos, un nombre es una mancha: se apaga la capa entera en vez de dibujar texto ilegible
    //: encima de las esferas.
    var pos = this._graph.camera().position;
    var lejos = Math.hypot(pos.x, pos.y, pos.z) > 1500;
    if (lejos) {
      for (var k = 0; k < this._labels.children.length; k += 1) {
        this._labels.children[k].style.opacity = "0";
      }
      return;
    }
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

  //: Apagar un tipo de nodo: mismo estado que el mapa 2D, aplicado a las esferas.
  Graph3D.prototype.setLabelVisible = function (kind, visible) {
    this._hidden[kind] = !visible;
    if (this._graph && this._graph.refresh) { this._graph.refresh(); }
    return !this._hidden[kind];
  };

  Graph3D.prototype.isHidden = function (kind) {
    return !!this._hidden[kind];
  };

  /* Acercar y alejar la cámara.
   *
   * Se mueve la cámara sobre su propia dirección: `zoom` del motor no existe acá, y cambiar el
   * encuadre de golpe (zoomToFit) perdería el punto que la persona estaba mirando.
   */
  Graph3D.prototype.zoomBy = function (factor) {
    if (!this._graph) { return; }
    var camera = this._graph.camera();
    var pos = camera.position;
    var len = Math.hypot(pos.x, pos.y, pos.z) || 1;
    var next = Math.max(60, Math.min(4000, len / (factor || 1)));
    var k = next / len;
    this._graph.cameraPosition({ x: pos.x * k, y: pos.y * k, z: pos.z * k }, null, 320);
  };

  Graph3D.prototype.reset = function () {
    this.clearTrail();
    this.fit(700);
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
    //: 40 de relleno en vez de 24: con 1.437 nodos, el encuadre ajustado dejaba la estructura
    //: fuera de cuadro y las etiquetas encima de los nodos.
    this._graph.zoomToFit(duration === undefined ? 900 : duration, 40);
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
