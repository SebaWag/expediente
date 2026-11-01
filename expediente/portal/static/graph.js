/* The graph canvas: two cameras over one structure, and the verbs on the edges.
 *
 * ## Why both 2D and 3D exist, and what each one is for
 *
 * 2D is the *map*: a layered silhouette where detectors sit in the core, the signals they
 * emitted form the first ring, the orders they observe the second, and the documents that
 * accredit them the outside. Nothing overlaps and a stranger can read the whole system in one
 * look. That is why it is the default.
 *
 * 3D is the *walk*: the same rings become shells, and moving the camera is how you understand
 * that a supplier's neighbourhood is a volume, not a disc. Perspective also solves a problem
 * 2D has no answer for — when two nodes sit at the same screen position, depth tells them apart.
 *
 * 3D only works with depth cues, and they are all present on purpose:
 *
 *   1. **Size** falls with distance: radius is multiplied by the projected factor.
 *   2. **Dimming** falls with distance: far nodes fade toward the field, they never stay at
 *      full opacity (a flat 3D graph reads as a 2D graph with extra spaghetti).
 *   3. **Line weight** falls with distance, so the eye sees which edges are in front.
 *   4. **Painter's order**: everything is drawn back to front, so occlusion is real.
 *   5. **Labels are billboards**, drawn after the geometry and only for the front half. Labels
 *      that travel with the nodes turn a readable graph into a pile of overlapping pills.
 *
 * ## The rule this file refuses to break
 *
 * Every visual change is driven by data that exists. A node is lit because the edge is there, a
 * claim turns red because the recording says it was rejected, a path is drawn one hop at a time
 * because there are as many hops as there are recorded steps. There is no decorative animation
 * that suggests work that did not happen — the interface is not allowed to claim more than the
 * run does.
 */
(function (global) {
  "use strict";

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

  //: The shell (3D) and ring (2D) radius per label. Detectors in the core, the documents that
  //: accredit everything on the outside: the picture states what the system does, in layers.
  var RING = {
    Detector: 0.0,
    Senal: 0.36,
    CompraAgil: 0.52,
    Licitacion: 0.52,
    Proveedor: 0.55,
    Organismo: 0.55,
    OrdenCompra: 0.68,
    Documento: 0.9,
    Evidencia: 0.9,
    Afirmacion: 0.78,
    Domicilio: 0.78
  };

  //: What each predicate means, in the words a stranger needs. The edge label says
  //: `ADJUDICADA_A`; the legend is where that becomes a sentence.
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

  //: The legend speaks to the reader the dossier speaks to. `OrdenCompra` is the schema; "orden
  //: de compra" is the thing an auditor signs.
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

  var FOV = 2.55;        // > 1: a gentle perspective. A wide one bulges the middle of the field.
  var NEAR = 90;         // world units: closer than this the node is behind the camera
  var PITCH_LIMIT = 1.15;

  function clamp(value, low, high) {
    return value < low ? low : value > high ? high : value;
  }

  function radialPoint(index, total, radius, width, height, random) {
    var angle = (index / Math.max(1, total)) * Math.PI * 2 + (random() - 0.5) * 0.05;
    var spread = radius * Math.min(width, height) * 0.46;
    return {
      x: width / 2 + Math.cos(angle) * spread * 1.18,
      y: height / 2 + Math.sin(angle) * spread * 0.86,
      z: 0
    };
  }

  function brainPoint(width, height, random) {
    // Two overlapping ellipses, tilted, with the seam left empty. Points outside both
    // are rejected, which is what gives the silhouette its shape instead of a blob.
    for (var attempt = 0; attempt < 40; attempt += 1) {
      var side = random() < 0.5 ? -1 : 1;
      var cx = width / 2 + side * width * 0.17;
      var cy = height / 2;
      var rx = width * 0.26;
      var ry = height * 0.36;
      var angle = random() * Math.PI * 2;
      var radius = Math.sqrt(random());
      var x = cx + Math.cos(angle) * rx * radius;
      var y = cy + Math.sin(angle) * ry * radius;
      // the fissure: keep a gap down the middle
      if (Math.abs(x - width / 2) < width * 0.035 && random() < 0.8) { continue; }
      // no corners
      if (y < height * 0.08 || y > height * 0.92) { continue; }
      return { x: x, y: y, z: 0 };
    }
    return { x: width / 2, y: height / 2, z: 0 };
  }

  //: Fibonacci sphere: the only cheap way to spread N points evenly on a shell without
  //: clustering at the poles. A random sphere puts a knot at both poles and the picture
  //: reads as broken, not as random.
  function shellPoint(index, total, radius, width, height, random) {
    var spread = radius * Math.min(width, height) * 0.46;
    var golden = Math.PI * (3 - Math.sqrt(5));
    var t = (index + 0.5) / Math.max(1, total);
    var y = 1 - 2 * t;
    var ring = Math.sqrt(Math.max(0, 1 - y * y));
    var theta = index * golden + (random() - 0.5) * 0.28;
    var jitter = 1 + (random() - 0.5) * 0.16;
    return {
      x: width / 2 + Math.cos(theta) * ring * spread * jitter,
      y: height / 2 + y * spread * jitter,
      z: Math.sin(theta) * ring * spread * jitter
    };
  }


  //: Shape per label. Colour alone cannot carry eleven categories — a colour-blind reader loses
  //: all of them, and a projector loses three. Shape is the encoding that survives a bad
  //: projector, a screenshot and a printed dossier.
  var SHAPES = {
    Organismo: "hex",
    Proveedor: "circle",
    Domicilio: "diamond",
    Persona: "diamond",
    OrdenCompra: "square",
    CompraAgil: "square",
    Licitacion: "square",
    Documento: "page",
    Evidencia: "page",
    Senal: "triangle",
    Afirmacion: "pentagon",
    Detector: "circle"
  };

  function shapeFor(label) {
    return SHAPES[label] || "circle";
  }

  function polygon(ctx, x, y, sides, radius, rotation) {
    ctx.beginPath();
    for (var i = 0; i < sides; i += 1) {
      var angle = rotation + (i / sides) * Math.PI * 2;
      var px = x + Math.cos(angle) * radius;
      var py = y + Math.sin(angle) * radius;
      if (i === 0) { ctx.moveTo(px, py); } else { ctx.lineTo(px, py); }
    }
    ctx.closePath();
  }

  function drawShape(ctx, x, y, radius, shape) {
    var r = Math.max(1.2, radius);
    if (shape === "circle") {
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      return;
    }
    if (shape === "square") {
      ctx.beginPath();
      if (ctx.roundRect) { ctx.roundRect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76, r * 0.28); }
      else { ctx.rect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76); }
      ctx.fill();
      return;
    }
    if (shape === "page") {
      // A document reads as a sheet: rectangle with a folded corner.
      var w = r * 1.5;
      ctx.beginPath();
      ctx.moveTo(x - w / 2, y - r);
      ctx.lineTo(x + w / 2 - r * 0.55, y - r);
      ctx.lineTo(x + w / 2, y - r + r * 0.55);
      ctx.lineTo(x + w / 2, y + r);
      ctx.lineTo(x - w / 2, y + r);
      ctx.closePath();
      ctx.fill();
      return;
    }
    if (shape === "diamond") {
      ctx.beginPath();
      ctx.moveTo(x, y - r * 1.2);
      ctx.lineTo(x + r, y);
      ctx.lineTo(x, y + r * 1.2);
      ctx.lineTo(x - r, y);
      ctx.closePath();
      ctx.fill();
      return;
    }
    if (shape === "triangle") { polygon(ctx, x, y + r * 0.14, 3, r * 1.22, -Math.PI / 2); ctx.fill(); return; }
    if (shape === "pentagon") { polygon(ctx, x, y, 5, r * 1.15, -Math.PI / 2); ctx.fill(); return; }
    if (shape === "hex") { polygon(ctx, x, y, 6, r * 1.12, Math.PI / 6); ctx.fill(); return; }
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  function GraphView(canvas, options) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.options = options || {};
    this.nodes = [];
    this.edges = [];
    this.scale = 1;
    this.tx = 0;
    this.ty = 0;
    this.zoom = 1;
    this.selected = null;
    this.hovered = null;
    this.hoveredEdge = null;
    this.pinned = [];
    this.peek = [];
    this.hidden = {};
    this.solo = null;
    this.pulse = null;
    this.path = null;
    this._fly = null;
    this._time = 0;
    this._raf = null;
    this._dragged = 0;

    this.mode = "2d";
    try {
      var saved = global.localStorage && global.localStorage.getItem("expediente.vista");
      if (saved === "3d" || saved === "2d") { this.mode = saved; }
    } catch (error) { /* private mode: 2D is the default, and that is fine */ }

    this.camera = { yaw: -0.55, pitch: 0.32, dist: 1500 };
    this._bind();
  }

  GraphView.prototype.colorFor = function (label) {
    return COLORS[label] || "#94a3b8";
  };

  GraphView.prototype.setMode = function (mode) {
    if (mode !== "2d" && mode !== "3d") { return this.mode; }
    if (mode === this.mode) { return this.mode; }
    this.mode = mode;
    try {
      if (global.localStorage) { global.localStorage.setItem("expediente.vista", mode); }
    } catch (error) { /* nothing to do: the view still works for this session */ }
    if (mode === "3d") {
      this._lift();
      // Switching after the data arrived is the normal path (the toggle lives in the header), so
      // the layout has to happen here too, not only inside setData. Without this the graph keeps
      // the 2D seed and nine nodes end up piled on one side of an empty canvas.
      if (this.nodes.length <= 80) {
        this._settle3d(320);
        this._fitToNodes(1.15);
      }
    }
    this.render();
    this._animate();
    return this.mode;
  };

  GraphView.prototype.toggleMode = function () {
    return this.setMode(this.mode === "2d" ? "3d" : "2d");
  };

  //: Give every node that has no height one, without moving the ones that already do. The
  //: switch must be cheap and reversible: a person flips it mid-conversation to compare.
  GraphView.prototype._lift = function () {
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var byLabel = {};
    this.nodes.forEach(function (node) { byLabel[node.label] = (byLabel[node.label] || 0) + 1; });
    var seen = {};
    this.nodes.forEach(function (node) {
      var index = seen[node.label] || 0;
      seen[node.label] = index + 1;
      if (node.z !== undefined && node._lifted) { return; }
      var spot = shellPoint(index, byLabel[node.label], RING[node.label] !== undefined ? RING[node.label] : 0.5, width, height, Math.random);
      node.x = spot.x;
      node.y = spot.y;
      node.z = spot.z;
      node._lifted = true;
    });
    this._fitCamera();
  };

  GraphView.prototype._fitCamera = function () {
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var radius = Math.min(width, height) * 0.46 * 0.95;
    this.camera.dist = Math.max(520, radius / Math.tan(FOV / 2) * 2.2);
    this.camera.target = { x: width / 2, y: height / 2, z: 0 };
  };

  GraphView.prototype.center = function () {
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    return { x: width / 2, y: height / 2 };
  };

  /* Project a world point to the screen.
   *
   * Both cameras answer the same three questions — where is it, how big is it, how far is it —
   * so the rest of the file never asks which mode it is in. */
  //: Pure projection: no state, no side effects, so a fit can try a camera before using it.
  GraphView.prototype._projectionOf = function (x, y, z, yaw, pitch, dist, target, zoom, width, height) {
    var dx = x - target.x;
    var dy = y - target.y;
    var dz = z || 0;

    var cy = Math.cos(yaw);
    var sy = Math.sin(yaw);
    var x1 = dx * cy - dz * sy;
    var z1 = dx * sy + dz * cy;

    var cp = Math.cos(pitch);
    var sp = Math.sin(pitch);
    var y1 = dy * cp - z1 * sp;
    var z2 = dy * sp + z1 * cp;

    var depth = dist + z2;
    if (depth < NEAR) { return { sx: 0, sy: 0, f: 0, depth: depth, visible: false }; }
    var f = (dist / depth) * zoom;
    return { sx: width / 2 + x1 * f, sy: height / 2 + y1 * f, f: f, depth: depth, visible: true };
  };

  GraphView.prototype._project = function (x, y, z) {
    if (this.mode === "2d") {
      return { sx: x * this.scale + this.tx, sy: y * this.scale + this.ty, f: this.scale, depth: 0, visible: true };
    }
    var cam = this.camera;
    var target = cam.target || this.center();
    return this._projectionOf(x, y, z, cam.yaw, cam.pitch, cam.dist, target, this.zoom,
      this.canvas.clientWidth || 900, this.canvas.clientHeight || 700);
  };

  //: How much of its colour a node keeps, by depth. This single number is the difference
  //: between "3D" and "a 2D graph with a misleading perspective".
  GraphView.prototype._depthAlpha = function (depth) {
    if (this.mode === "2d") { return 1; }
    var cam = this.camera;
    var near = cam.dist * 0.55;
    var far = cam.dist * 1.75;
    var t = clamp((depth - near) / (far - near), 0, 1);
    return 1.06 - t * 0.72;
  };

  GraphView.prototype._bind = function () {
    var self = this;
    var dragging = null;
    var orbiting = false;
    var panning = false;
    var last = { x: 0, y: 0 };
    var moved = 0;

    function point(event) {
      var rect = self.canvas.getBoundingClientRect();
      var source = event.touches ? event.touches[0] : event;
      return { x: source.clientX - rect.left, y: source.clientY - rect.top };
    }

    function find(at) {
      var best = null;
      var bestDistance = 26;
      for (var i = 0; i < self.nodes.length; i += 1) {
        var node = self.nodes[i];
        if (self.isHidden(node.label) || !node._visible || node._sx === undefined) { continue; }
        var dx = node._sx - at.x;
        var dy = node._sy - at.y;
        var distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < Math.max(bestDistance, node._r + 5) && (!best || distance < bestDistance)) {
          best = node;
          bestDistance = distance;
        }
      }
      return best;
    }

    function findEdge(at) {
      var best = null;
      var bestDistance = 9;
      for (var i = 0; i < self.edges.length; i += 1) {
        var edge = self.edges[i];
        if (!edge.source._visible || !edge.target._visible) { continue; }
        if (self.isHidden(edge.source.label) || self.isHidden(edge.target.label)) { continue; }
        var mx = (edge.source._sx + edge.target._sx) / 2;
        var my = (edge.source._sy + edge.target._sy) / 2;
        var distance = distanceToCurve(at, edge.source._sx, edge.source._sy, mx, my, edge.target._sx, edge.target._sy);
        if (distance < bestDistance) {
          best = edge;
          bestDistance = distance;
        }
      }
      return best;
    }

    function distanceToCurve(at, x0, y0, cx, cy, x1, y1) {
      // The edges are drawn as quadratics; sampling is enough for a 9 px hit radius and it is
      // far cheaper than solving the cubic that finds the exact nearest point.
      var best = Infinity;
      for (var step = 0; step <= 12; step += 1) {
        var t = step / 12;
        var u = 1 - t;
        var x = u * u * x0 + 2 * u * t * cx + t * t * x1;
        var y = u * u * y0 + 2 * u * t * cy + t * t * y1;
        var distance = Math.sqrt((x - at.x) * (x - at.x) + (y - at.y) * (y - at.y));
        if (distance < best) { best = distance; }
      }
      return best;
    }

    this.canvas.addEventListener("pointerdown", function (event) {
      var at = point(event);
      var node = find(at);
      moved = 0;
      last = at;
      if (node) {
        dragging = node;
        self.canvas.setPointerCapture && self.canvas.setPointerCapture(event.pointerId);
        return;
      }
      if (self.mode === "3d" && !event.shiftKey) {
        orbiting = true;
      } else {
        panning = true;
      }
      self.canvas.setPointerCapture && self.canvas.setPointerCapture(event.pointerId);
    });

    this.canvas.addEventListener("pointermove", function (event) {
      var at = point(event);
      var dx = at.x - last.x;
      var dy = at.y - last.y;
      moved += Math.abs(dx) + Math.abs(dy);

      if (dragging) {
        var per = self.mode === "2d" ? self.scale : (dragging._f || 1);
        dragging.x += dx / per;
        dragging.y += dy / per;
        self.render();
      } else if (orbiting) {
        self.camera.yaw += dx * 0.0055;
        self.camera.pitch = clamp(self.camera.pitch + dy * 0.0045, -PITCH_LIMIT, PITCH_LIMIT);
        self.render();
      } else if (panning) {
        if (self.mode === "3d" && self.camera.target) {
          self.camera.target.x -= dx * (self.camera.dist / 1200);
          self.camera.target.y -= dy * (self.camera.dist / 1200);
        } else {
          self.tx += dx;
          self.ty += dy;
        }
        self.render();
      } else {
        var node = find(at);
        var edge = node ? null : findEdge(at);
        if (node !== self.hovered || edge !== self.hoveredEdge) {
          self.hovered = node;
          self.hoveredEdge = edge;
          self.canvas.style.cursor = node ? "pointer" : edge ? "help" : "grab";
          self.render();
        }
      }
      last = at;
    });

    this.canvas.addEventListener("pointerup", function (event) {
      var wasDragging = dragging;
      dragging = null;
      orbiting = false;
      panning = false;
      self._dragged = moved;
      if (moved > 6) { return; }
      var at = point(event);
      var node = find(at);
      if (node) {
        // Shift-click pins a second node instead of replacing the selection: the path question
        // needs two subjects, and a mode you have to enter is a mode nobody finds.
        if (event.shiftKey || event.metaKey) {
          var pinned = self.pin(node.id);
          if (self.options.onPin) { self.options.onPin(node, pinned); }
          return;
        }
        self.selected = node.id;
        self.pinned = [];
        self.render();
        if (self.options.onSelect) { self.options.onSelect(node); }
        return;
      }
      var edge = findEdge(at);
      if (edge && self.options.onEdge) {
        self.options.onEdge({ source: edge.source, target: edge.target, rel: edge.rel });
        return;
      }
      if (wasDragging) { self.render(); }
    });

    this.canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      self.zoomAt(factor, point(event));
    }, { passive: false });

    this.canvas.addEventListener("dblclick", function (event) {
      var at = point(event);
      var node = find(at);
      if (node && self.options.onExpand) { self.options.onExpand(node); }
    });

    this.canvas.addEventListener("pointerleave", function () {
      if (self.hovered || self.hoveredEdge) {
        self.hovered = null;
        self.hoveredEdge = null;
        self.render();
      }
    });
  };

  GraphView.prototype.zoomAt = function (factor, at) {
    if (this.mode === "2d") {
      var width = this.canvas.clientWidth || 900;
      var spot = at || { x: width / 2, y: this.canvas.clientHeight / 2 };
      var before = { x: (spot.x - this.tx) / this.scale, y: (spot.y - this.ty) / this.scale };
      this.scale = clamp(this.scale * factor, 0.25, 9);
      this.tx = spot.x - before.x * this.scale;
      this.ty = spot.y - before.y * this.scale;
    } else {
      // In perspective, zooming is a dolly: moving the camera is what changes scale, and
      // multiplying a factor over the projection instead would break the depth fog.
      this.camera.dist = clamp(this.camera.dist / factor, 200, 9000);
    }
    this.render();
  };

  GraphView.prototype.setData = function (data, options) {
    var merge = !!(options && options.merge) && this.nodes.length > 0;
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var byId = {};
    var self = this;
    var seeded = 0;

    if (merge) {
      this.nodes.forEach(function (node) { byId[node.id] = node; });
    }

    var byLabel = {};
    (data.nodes || []).forEach(function (raw) {
      byLabel[raw.label] = (byLabel[raw.label] || 0) + 1;
    });
    var seenByLabel = {};
    var radial = data.layout === "radial" && !merge;

    var added = (data.nodes || []).map(function (raw) {
      if (merge && byId[raw.id]) {
        byId[raw.id].data = raw;
        byId[raw.id].name = raw.name;
        return null;
      }
      var spot;
      if (self.mode === "3d" && !merge) {
        var position = seenByLabel[raw.label] || 0;
        seenByLabel[raw.label] = position + 1;
        spot = shellPoint(position, byLabel[raw.label], RING[raw.label] !== undefined ? RING[raw.label] : 0.5, width, height, Math.random);
      } else if (radial) {
        var seen = seenByLabel[raw.label] || 0;
        seenByLabel[raw.label] = seen + 1;
        spot = radialPoint(seen, byLabel[raw.label], RING[raw.label] !== undefined ? RING[raw.label] : 0.5, width, height, Math.random);
      } else {
        spot = brainPoint(width, height, Math.random);
      }
      seeded += 1;
      var entry = {
        id: raw.id,
        label: raw.label,
        name: raw.name || raw.id,
        data: raw,
        x: spot.x,
        y: spot.y,
        z: spot.z || 0,
        vx: 0,
        vy: 0,
        degree: 0,
        r: raw.label === "Detector" ? 16 : raw.label === "Senal" ? 8 : 5
      };
      byId[entry.id] = entry;
      return entry;
    }).filter(Boolean);

    var newEdges = (data.edges || []).filter(function (edge) {
      return byId[edge.source] && byId[edge.target];
    }).map(function (edge) {
      return { source: byId[edge.source], target: byId[edge.target], rel: edge.rel };
    });

    if (merge) {
      var seenKey = {};
      this.edges.forEach(function (edge) { seenKey[edge.source.id + "|" + edge.rel + "|" + edge.target.id] = true; });
      newEdges = newEdges.filter(function (edge) {
        return !seenKey[edge.source.id + "|" + edge.rel + "|" + edge.target.id];
      });
    }

    this.nodes = merge ? this.nodes.concat(added) : added;
    this.edges = merge ? this.edges.concat(newEdges) : newEdges;

    this.nodes.forEach(function (node) { node.degree = 0; });
    this.edges.forEach(function (edge) {
      edge.source.degree += 1;
      edge.target.degree += 1;
    });
    this.nodes.forEach(function (node) {
      var base = node.label === "Detector" ? 26 : node.label === "Senal" ? 7 : 4.5;
      node.r = base + Math.min(6, Math.sqrt(node.degree) * 1.4);
    });

    this.selected = data.seed || this.selected;
    if (!merge) {
      this.scale = 1;
      this.tx = 0;
      this.ty = 0;
      this.zoom = 1;
      if (this.mode === "3d") { this._fitCamera(); }
    }
    // A radial layout is already the answer: relaxing it would pull it back toward a
    // hairball. Only the merged neighbourhoods get the force pass.
    if (this.mode === "3d" && !merge && this.nodes.length <= 80) {
      this._settle3d(320);
      this._fitToNodes(1.15);
    } else {
      this._settle(merge && this.mode === "2d" ? 90 : radial || this.mode === "3d" ? 0 : 220);
    }
    this.render();
    this._animate();
  };


  /* A neighbourhood gets a force layout in three dimensions; a corpus gets shells.
   *
   * The shells answer "where does this label live in the system", which is the right question for
   * a map of everything. It is the wrong answer for nine nodes: placing each one by its index
   * inside a shell clusters them on one side and leaves 80% of the canvas empty, which is what a
   * UX review of the first version said in the first line. Below 80 nodes the springs take over
   * and the picture fills its frame.
   */
  GraphView.prototype._settle3d = function (iterations) {
    var nodes = this.nodes;
    var edges = this.edges;
    if (!nodes.length) { return; }
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var cx = width / 2;
    var cy = height / 2;
    var cz = 0;

    for (var step = 0; step < iterations; step += 1) {
      var cooling = 1 - step / iterations;
      for (var i = 0; i < nodes.length; i += 1) {
        var a = nodes[i];
        for (var j = i + 1; j < nodes.length; j += 1) {
          var b = nodes[j];
          var dx = b.x - a.x;
          var dy = b.y - a.y;
          var dz = (b.z || 0) - (a.z || 0);
          var d2 = dx * dx + dy * dy + dz * dz || 0.01;
          var d = Math.sqrt(d2);
          // A minimum separation measured in node radii: two small nodes need less air than two
          // detectors, and a spring constant alone never learns that.
          var want = (a.r + b.r) * 7.5;
          var force = (Math.min(1600, 26000 / d2) + Math.max(0, want - d) * 0.06) * cooling;
          var fx = (dx / d) * force;
          var fy = (dy / d) * force;
          var fz = (dz / d) * force;
          a.vx -= fx; a.vy -= fy; (a.vz = (a.vz || 0) - fz);
          b.vx += fx; b.vy += fy; (b.vz = (b.vz || 0) + fz);
        }
      }
      for (var k = 0; k < edges.length; k += 1) {
        var edge = edges[k];
        var ex = edge.target.x - edge.source.x;
        var ey = edge.target.y - edge.source.y;
        var ez = (edge.target.z || 0) - (edge.source.z || 0);
        var length = Math.sqrt(ex * ex + ey * ey + ez * ez) || 0.01;
        var pull = (length - 210) * 0.014 * cooling;
        var px = (ex / length) * pull;
        var py = (ey / length) * pull;
        var pz = (ez / length) * pull;
        edge.source.vx += px; edge.source.vy += py; edge.source.vz = (edge.source.vz || 0) + pz;
        edge.target.vx -= px; edge.target.vy -= py; edge.target.vz = (edge.target.vz || 0) - pz;
      }
      for (var m = 0; m < nodes.length; m += 1) {
        var node = nodes[m];
        node.vx += (cx - node.x) * 0.010 * cooling;
        node.vy += (cy - node.y) * 0.010 * cooling;
        node.vz = (node.vz || 0) + (cz - (node.z || 0)) * 0.006 * cooling;
        node.x += node.vx;
        node.y += node.vy;
        node.z = (node.z || 0) + node.vz;
        node.vx *= 0.62; node.vy *= 0.62; node.vz = node.vz * 0.62;
      }
    }
  };

  GraphView.prototype._settle = function (iterations) {
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var nodes = this.nodes;
    var edges = this.edges;
    var cx = width / 2;
    var cy = height / 2;

    for (var step = 0; step < iterations; step += 1) {
      var cooling = 1 - step / iterations;
      for (var i = 0; i < nodes.length; i += 1) {
        var a = nodes[i];
        for (var j = i + 1; j < nodes.length; j += 1) {
          var b = nodes[j];
          var dx = b.x - a.x;
          var dy = b.y - a.y;
          var d2 = dx * dx + dy * dy || 0.01;
          var d = Math.sqrt(d2);
          var force = Math.min(900, 5200 / d2) * cooling;
          var fx = (dx / d) * force;
          var fy = (dy / d) * force;
          a.vx -= fx; a.vy -= fy;
          b.vx += fx; b.vy += fy;
        }
      }
      for (var k = 0; k < edges.length; k += 1) {
        var edge = edges[k];
        var ex = edge.target.x - edge.source.x;
        var ey = edge.target.y - edge.source.y;
        var ed = Math.sqrt(ex * ex + ey * ey) || 0.01;
        var pull = (ed - 110) * 0.012 * cooling;
        var px = (ex / ed) * pull;
        var py = (ey / ed) * pull;
        edge.source.vx += px; edge.source.vy += py;
        edge.target.vx -= px; edge.target.vy -= py;
      }
      for (var m = 0; m < nodes.length; m += 1) {
        var node = nodes[m];
        node.vx += (cx - node.x) * 0.0016 * cooling;
        node.vy += (cy - node.y) * 0.0016 * cooling;
        node.x += node.vx;
        node.y += node.vy;
        node.vx *= 0.72;
        node.vy *= 0.72;
      }
    }
  };

  GraphView.prototype.flyTo = function (nodeId, zoom) {
    var node = null;
    for (var i = 0; i < this.nodes.length; i += 1) {
      if (this.nodes[i].id === nodeId) { node = this.nodes[i]; break; }
    }
    if (!node) { return false; }
    if (this.mode === "3d") {
      var cam = this.camera;
      var target = cam.target || this.center();
      this._fly = {
        start: performance.now(),
        duration: 780,
        from: { yaw: cam.yaw, pitch: cam.pitch, dist: cam.dist, tx: target.x, ty: target.y, tz: target.z || 0 },
        to: {
          yaw: cam.yaw,
          pitch: clamp(cam.pitch, -PITCH_LIMIT, PITCH_LIMIT),
          dist: 620 / (zoom || 1.6),
          tx: node.x,
          ty: node.y,
          tz: node.z || 0
        }
      };
    } else {
      var width = this.canvas.clientWidth || 900;
      var height = this.canvas.clientHeight || 700;
      var scale = zoom || 1.8;
      this._fly = {
        start: performance.now(),
        duration: 780,
        from: { scale: this.scale, tx: this.tx, ty: this.ty },
        to: { scale: scale, tx: width / 2 - node.x * scale, ty: height / 2 - node.y * scale }
      };
    }
    this.pulse = { id: node.id, start: performance.now() };
    this.selected = node.id;
    this._animate();
    return true;
  };

  /* Fit the camera to everything drawn, immediately.
   *
   * `frameNodes` animates because a person asked for it; this one is for the moment a layout just
   * finished and there is nobody to watch an easing curve. The floor on the distance is gone: it
   * was there to stop an empty graph from dividing by zero and it ended up framing nine nodes as
   * if they were a hemisphere. */
  GraphView.prototype._fitToNodes = function (padding) {
    if (this.mode !== "3d" || !this.nodes.length) { return; }
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var reserved = this.options.legend ? 270 : 0;
    var margin = padding || 1.0;

    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    var self = this;
    this.nodes.forEach(function (node) {
      minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x);
      minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y);
    });
    var span = Math.max(maxX - minX, maxY - minY, 120);
    var cam = this.camera;
    var target = { x: (minX + maxX) / 2, y: (minY + maxY) / 2, z: 0 };
    var zoom = 1;
    var dist = Math.max(300, span * 2.2);

    /* Fit in screen pixels, twice.
     *
     * The naive version divided a world span by the available width and called it done, which is
     * only true when the perspective factor is exactly 1 — it never is, because a node nearer the
     * camera projects larger. The result was a two-company case that ran 176 px off the right edge.
     * So: propose a camera, project every node with it, measure the box on screen, correct, repeat. */
    for (var pass = 0; pass < 3; pass += 1) {
      var sxMin = Infinity, sxMax = -Infinity, syMin = Infinity, syMax = -Infinity;
      this.nodes.forEach(function (node) {
        var p = self._projectionOf(node.x, node.y, node.z || 0, cam.yaw, cam.pitch, dist, target, zoom, width, height);
        if (!p.visible) { return; }
        // A node's footprint on screen is its dot *plus its label*, which hangs below it and can be
        // 150 px wide. Fitting only the dots puts the text — the part that carries the meaning —
        // outside the frame.
        var halfWidth = Math.max(70, node._r * 2);
        sxMin = Math.min(sxMin, p.sx - halfWidth); sxMax = Math.max(sxMax, p.sx + halfWidth);
        syMin = Math.min(syMin, p.sy - node._r);
        syMax = Math.max(syMax, p.sy + node._r + 30);
      });
      if (!isFinite(sxMin)) { break; }
      var wide = Math.max(1, sxMax - sxMin);
      var tall = Math.max(1, syMax - syMin);
      var roomW = (width - reserved) / margin;
      var roomH = height / margin;
      var factor = Math.min(roomW / wide, roomH / tall);
      zoom = clamp(zoom * factor, 0.2, 8);

      // Where the box landed, versus where it should land: the centre of the free rectangle.
      var landedX = (sxMin + sxMax) / 2;
      var landedY = (syMin + syMax) / 2;
      var shouldX = (width - reserved) / 2;
      var shouldY = height / 2;
      // Moving the target moves the picture the other way, and by `zoom` pixels per world unit.
      target.x -= (shouldX - landedX) / zoom;
      target.y -= (shouldY - landedY) / zoom;
    }

    this.camera.target = { x: target.x, y: target.y, z: 0 };
    this.camera.dist = dist;
    this.zoom = zoom;
  };

  //: Frame a set of nodes — used by the path view and by "show me the neighbourhood".
  GraphView.prototype.frameNodes = function (ids, padding) {
    if (this.mode !== "3d") {
      // In 2D the camera is a scale and an origin, so framing a set is arithmetic. Flying to the
      // first node alone left the other end of the route off screen.
      var wanted2d = {};
      ids.forEach(function (id) { wanted2d[id] = true; });
      var picked2d = this.nodes.filter(function (node) { return wanted2d[node.id]; });
      if (!picked2d.length) { return false; }
      var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      picked2d.forEach(function (node) {
        minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x);
        minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y);
      });
      var width2d = this.canvas.clientWidth || 900;
      var height2d = this.canvas.clientHeight || 700;
      var spanX = Math.max(maxX - minX, 120);
      var spanY = Math.max(maxY - minY, 120);
      // Room for the labels and for the floating panel along the bottom.
      // The route panel occupies the right-hand 430 px. Centring the drawing in the whole canvas
      // put one end of the route underneath it — a review counted "a two-hop path with only one and
      // a half hops visible".
      var panel = 470;
      var roomX = Math.max(320, width2d - panel) * 0.66;
      var roomY = height2d * 0.50;
      var scale = clamp(Math.min(roomX / spanX, roomY / spanY), 0.4, 4.5);
      this.scale = scale;
      this.tx = (width2d - panel) * 0.5 - ((minX + maxX) / 2) * scale;
      this.ty = height2d * 0.46 - ((minY + maxY) / 2) * scale;
      this.pulse = { id: ids[0], start: performance.now() };
      this.render();
      this._animate();
      return true;
    }
    var wanted = {};
    ids.forEach(function (id) { wanted[id] = true; });
    var picked = this.nodes.filter(function (node) { return wanted[node.id]; });
    if (!picked.length) { return false; }
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;
    picked.forEach(function (node) {
      minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x);
      minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y);
      minZ = Math.min(minZ, node.z || 0); maxZ = Math.max(maxZ, node.z || 0);
    });
    var span = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 220) * (padding || 1.3);
    var cam = this.camera;
    this._fly = {
      start: performance.now(),
      duration: 720,
      from: { yaw: cam.yaw, pitch: cam.pitch, dist: cam.dist, tx: cam.target.x, ty: cam.target.y, tz: cam.target.z || 0 },
      to: {
        yaw: cam.yaw,
        pitch: cam.pitch,
        dist: Math.max(360, span / Math.tan(FOV / 2) * 1.1),
        tx: (minX + maxX) / 2,
        ty: (minY + maxY) / 2,
        tz: (minZ + maxZ) / 2
      }
    };
    this._animate();
    return true;
  };

  GraphView.prototype._animate = function () {
    var self = this;
    if (this._raf) { return; }
    // Only while something is moving. A permanent 60 fps loop over 900 nodes is how a canvas
    // app heats a laptop for nothing, and a still frame is easier to record than a drifting one.
    function tick() {
      self._raf = null;
      self._time += 16;
      if (self._fly) {
        var now = performance.now();
        var t = clamp((now - self._fly.start) / self._fly.duration, 0, 1);
        var ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        var from = self._fly.from;
        var to = self._fly.to;
        Object.keys(to).forEach(function (key) {
          var value = from[key] + (to[key] - from[key]) * ease;
          if (key === "tx" || key === "ty" || key === "tz") {
            self.camera.target = self.camera.target || {};
            self.camera.target[key.slice(1)] = value;
          } else {
            self.camera[key] = value;
          }
        });
        if (t >= 1) { self._fly = null; }
      }
      if (self.pulse && performance.now() - self.pulse.start > 1700) { self.pulse = null; }
      self.render();
      if (self._fly || self.pulse) {
        self._raf = global.requestAnimationFrame(tick);
      } else {
        self._raf = null;
        self._time = 0;
      }
    }
    this._raf = global.requestAnimationFrame(tick);
  };

  GraphView.prototype.setLabelVisible = function (label, visible) {
    this.hidden[label] = !visible;
    this.render();
  };

  GraphView.prototype.isHidden = function (label) {
    if (this.solo && label !== this.solo) { return true; }
    return !!this.hidden[label];
  };

  GraphView.prototype.setSolo = function (label) {
    this.solo = this.solo === label ? null : label;
    this.render();
    return this.solo;
  };

  GraphView.prototype.counts = function () {
    var counts = {};
    this.nodes.forEach(function (node) { counts[node.label] = (counts[node.label] || 0) + 1; });
    return counts;
  };

  //: The predicates present in what is on screen, with how many edges each one accounts for.
  //: The legend is built from the data, so it cannot go stale.
  GraphView.prototype.relations = function () {
    var tally = {};
    var self = this;
    this.edges.forEach(function (edge) {
      if (self.isHidden(edge.source.label) || self.isHidden(edge.target.label)) { return; }
      tally[edge.rel] = (tally[edge.rel] || 0) + 1;
    });
    return Object.keys(tally).sort(function (a, b) { return tally[b] - tally[a]; }).map(function (rel) {
      return { rel: rel, count: tally[rel], meaning: REL_ES[rel] || rel };
    });
  };

  GraphView.prototype.zoomBy = function (factor) {
    this.zoomAt(factor, null);
  };

  GraphView.prototype.reset = function () {
    this.selected = null;
    this.pinned = [];
    this.path = null;
    if (this.mode === "3d") {
      this.zoom = 1;
      this._fitCamera();
    } else {
      this.scale = 1;
      this.tx = 0;
      this.ty = 0;
    }
    this.render();
  };

  //: Hint a route: the sequence of node ids the agent walked. Drawn as a bright, ordered
  //: trail so the path is the thing you see, not the field behind it.
  GraphView.prototype.showPath = function (ids, labels) {
    this.path = ids && ids.length ? { ids: ids, labels: labels || [] } : null;
    this.render();
  };

  GraphView.prototype.render = function () {
    var canvas = this.canvas;
    var ratio = global.devicePixelRatio || 1;
    var width = canvas.clientWidth || 900;
    var height = canvas.clientHeight || 700;

    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }

    var ctx = this.ctx;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    var wash = ctx.createRadialGradient(width / 2, height / 2, 40, width / 2, height / 2, Math.max(width, height) * 0.62);
    wash.addColorStop(0, this.mode === "3d" ? "rgba(56,189,248,0.10)" : "rgba(56,189,248,0.07)");
    wash.addColorStop(1, "rgba(2,6,23,0)");
    ctx.fillStyle = wash;
    ctx.fillRect(0, 0, width, height);

    var self = this;

    /* The route is computed before projecting, because the edges need it.
     *
     * An earlier version lifted the route toward the camera so the story sat "in front". It was
     * measured and rejected: moving a node in depth changes its projected size and position, and
     * in the two-company case it pushed one of the companies off the frame. The route is marked
     * with a ring and a brighter edge instead. A visual idea that loses the subject of the demo is
     * not an idea, it is a bug with a rationale. */
    var route = {};
    var routeIndex = {};
    if (this.path) {
      this.path.ids.forEach(function (id, index) {
        route[id] = true;
        routeIndex[id] = index;
      });
    }

    // Project once, then draw from the projection. Asking the camera a second time per draw
    // call is how a 60 fps canvas becomes a 20 fps canvas.
    this.nodes.forEach(function (node) {
      if (self.isHidden(node.label)) { node._visible = false; return; }
      var p = self._project(node.x, node.y, node.z || 0);
      node._sx = p.sx;
      node._sy = p.sy;
      node._f = p.f;
      node._depth = p.depth;
      node._visible = p.visible && p.sx > -600 && p.sx < width + 600 && p.sy > -600 && p.sy < height + 600;
      node._r = Math.max(1.2, node.r * (self.mode === "3d" ? p.f : 1));
      node._alpha = self._depthAlpha(p.depth);
    });

    /* Every node reserves its own circle before any text is placed.
     *
     * The first version only avoided other labels, so `ACREDITA` could be written straight over a
     * supplier's dot — the review found exactly that. Text may be dropped; text may not be laid on
     * top of the thing it describes. */
    var occupied = [];
    this.nodes.forEach(function (node) {
      if (!node._visible) { return; }
      var box = Math.max(9, node._r + 2);
      occupied.push({ x: node._sx - box, y: node._sy - box, w: box * 2, h: box * 2 });
    });

    var neighbour = {};
    if (this.peek && this.peek.length) {
      // A hover in the step waterfall lights the nodes that step touched, without changing the
      // selection: the log and the canvas are two views of one run, and reading one should move
      // the other. This is a *peek* — it ends when the pointer leaves.
      this.peek.forEach(function (id) { neighbour[id] = true; });
    }
    if (this.selected || this.pinned.length || this.path) {
      var lit = [this.selected].concat(this.pinned).filter(Boolean);
      // A route is a focus, not a decoration. Its nodes are the subject, so they join the mask and
      // everything else is pushed back — a highlight drawn on top of 900 lit nodes is not visible,
      // which is what a review of the first version said in one line: "the proof is invisible".
      if (this.path) { lit = lit.concat(this.path.ids); }
      if (this.peek) { lit = lit.concat(this.peek); }
      lit.forEach(function (id) { neighbour[id] = true; });
      this.edges.forEach(function (edge) {
        if (lit.indexOf(edge.source.id) >= 0) { neighbour[edge.target.id] = true; }
        if (lit.indexOf(edge.target.id) >= 0) { neighbour[edge.source.id] = true; }
      });
    }
    var focused = Object.keys(neighbour).length > 0;
    // With a route on screen the field is not context, it is noise: dim it harder than a selection.
    var dimTo = this.path ? 0.055 : 0.16;

    // Painter's algorithm: back to front, so a near node covers a far one.
    var order = this.nodes.slice().sort(function (a, b) { return (b._depth || 0) - (a._depth || 0); });
    var edgeOrder = this.edges.slice().sort(function (a, b) {
      return ((b.source._depth + b.target._depth) / 2) - ((a.source._depth + a.target._depth) / 2);
    });

    ctx.lineCap = "round";

    edgeOrder.forEach(function (edge) {
      if (!edge.source._visible || !edge.target._visible) { return; }
      var direct = focused && (neighbour[edge.source.id] && neighbour[edge.target.id]) &&
        (lit.indexOf(edge.source.id) >= 0 || lit.indexOf(edge.target.id) >= 0);
      var onRoute = route[edge.source.id] && route[edge.target.id] &&
        Math.abs(routeIndex[edge.source.id] - routeIndex[edge.target.id]) === 1;
      var inFocus = !focused || (neighbour[edge.source.id] || neighbour[edge.target.id]);
      var depth = ((edge.source._depth || 0) + (edge.target._depth || 0)) / 2;
      var alpha = (direct ? 0.85 : onRoute ? 1 : inFocus ? 0.22 : (self.path ? 0.015 : 0.05)) * self._depthAlpha(depth);
      var weight = (onRoute ? 3.6 : direct ? 1.9 : 0.8) * (self.mode === "3d" ? Math.max(0.35, edge.source._f + edge.target._f) / 2 : 1);
      ctx.lineWidth = Math.max(0.35, weight);
      ctx.strokeStyle = onRoute
        ? "rgba(125,211,252," + clamp(alpha + 0.15, 0, 1) + ")"
        : direct
          ? "rgba(148,197,255," + alpha + ")"
          : "rgba(148,163,184," + alpha + ")";
      var mx = (edge.source._sx + edge.target._sx) / 2;
      var my = (edge.source._sy + edge.target._sy) / 2;
      // A gentle control point off the midpoint: two nodes with several relations between them
      // draw as several lines instead of one, which is how you notice the relation is not one.
      var nx = -(edge.target._sy - edge.source._sy) * 0.06;
      var ny = (edge.target._sx - edge.source._sx) * 0.06;
      ctx.beginPath();
      ctx.moveTo(edge.source._sx, edge.source._sy);
      ctx.quadraticCurveTo(mx + nx, my + ny, edge.target._sx, edge.target._sy);
      ctx.stroke();
    });

    var drift = Math.sin(this._time / 2600) * 0.6;

    order.forEach(function (node) {
      if (!node._visible) { return; }
      var lit = !focused || neighbour[node.id];
      var colour = self.colorFor(node.label);
      var radius = node._r + (node === self.hovered ? 2 : 0);
      var depthAlpha = node._alpha * (lit ? 1 : (self.path ? 0.06 : 0.16));

      if (lit) {
        var glow = ctx.createRadialGradient(node._sx, node._sy, 0, node._sx, node._sy, radius * 3.4);
        glow.addColorStop(0, colour);
        glow.addColorStop(0.35, colour);
        glow.addColorStop(1, "rgba(0,0,0,0)");
        ctx.globalAlpha = (self.mode === "3d" ? 0.26 : 0.30) * depthAlpha;
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(node._sx, node._sy, radius * 3.4 + drift, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.globalAlpha = clamp(depthAlpha, 0, 1);
      ctx.fillStyle = colour;
      drawShape(ctx, node._sx, node._sy, radius, shapeFor(node.label));

      if (node.label === "Detector") {
        ctx.globalAlpha = 0.85 * node._alpha;
        ctx.strokeStyle = colour;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(node._sx, node._sy, radius + 7, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (node.id === self.selected || route[node.id]) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = route[node.id] ? "#38bdf8" : "#ffffff";
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.arc(node._sx, node._sy, radius + (node.label === "Detector" ? 13 : 6), 0, Math.PI * 2);
        ctx.stroke();
      }

      if (self.pulse && self.pulse.id === node.id) {
        var age = (performance.now() - self.pulse.start) / 1600;
        if (age < 1) {
          ctx.globalAlpha = Math.max(0, 1 - age);
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(node._sx, node._sy, radius + 8 + age * 46, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    });

    /* Labels last, as billboards, and never on top of each other.
     *
     * Three rules, in order of who wins:
     *
     *  1. **Priority owns the space.** A detector, the selection, the hovered node and anything on
     *     the route always get their label drawn, even if it lands on another one.
     *  2. **Everyone else is placed greedily** into the first free rectangle, and a label that
     *     would overlap an already-placed one is skipped rather than drawn on top of it. A graph
     *     with twelve readable labels beats one with sixty smudges — and the ones skipped are the
     *     ones the reader did not ask for.
     *  3. **Depth decides eligibility, not size.** In 3D, a label on a node behind the middle of
     *     the field is skipped: it is the label of a thing the reader cannot see properly anyway.
     */
    var depths = order.map(function (node) { return node._depth || 0; });
    var median = depths.length ? depths[Math.floor(depths.length / 2)] : 0;
    var limit = this.mode === "3d" ? median : Infinity;

    var candidates = [];
    order.forEach(function (node) {
      if (!node._visible) { return; }
      /* A small graph always names everything.
       *
       * The collision rule drops low-priority labels, which is right for a corpus and wrong for a
       * neighbourhood: with four nodes, hiding the fourth name leaves the reader unable to tell
       * what the second half of the finding is. The review caught exactly that — a person node
       * rendered nameless in the two-company case, so the shared-representative link was on screen
       * and unreadable. */
      var small = self.nodes.length <= 20;
      var priority = node.label === "Detector" ? 5
        : node.id === self.selected ? 4
          : node === self.hovered ? 4
            : route[node.id] ? 3
              : small ? 3
                : node.degree > 3 ? 2 : 1;
      var front = (node._depth || 0) <= limit;
      if (priority === 1 && (!front || node._r < 4.6 || node._alpha < 0.72)) { return; }
      candidates.push({ node: node, priority: priority, front: front });
    });
    candidates.sort(function (a, b) {
      if (a.priority !== b.priority) { return b.priority - a.priority; }
      return a.node._depth - b.node._depth;
    });

    var placed = occupied.slice();

    function overlaps(box) {
      for (var i = 0; i < placed.length; i += 1) {
        var other = placed[i];
        if (box.x < other.x + other.w && box.x + box.w > other.x
          && box.y < other.y + other.h && box.y + box.h > other.y) {
          return true;
        }
      }
      return false;
    }

    candidates.forEach(function (entry) {
      var node = entry.node;
      var isHub = node.label === "Detector";
      var text = String(node.name);
      if (text.length > 40) { text = text.slice(0, 38) + "…"; }
      var size = isHub ? 14 : node.id === self.selected || route[node.id] ? 13 : 12;
      ctx.font = (isHub ? "bold " : node.id === self.selected || route[node.id] ? "600 " : "") + size + "px ui-sans-serif, system-ui, sans-serif";
      var metrics = ctx.measureText(text);
      var pad = isHub ? 9 : 6;
      var boxHeight = size + (isHub ? 9 : 7);
      var bx = node._sx - metrics.width / 2 - pad;
      var by = node._sy + node._r + (isHub ? 13 : 7);
      var box = { x: bx, y: by, w: metrics.width + pad * 2, h: boxHeight };
      var must = entry.priority >= 3;
      if (!must && overlaps(box)) { return; }
      placed.push(box);

      ctx.globalAlpha = clamp(Math.max(node._alpha, 0.55), 0, 1);
      ctx.fillStyle = isHub ? "rgba(8,15,30,0.97)" : "rgba(2,6,23,0.90)";
      ctx.beginPath();
      if (ctx.roundRect) { ctx.roundRect(bx, by, box.w, box.h, isHub ? 7 : 5); }
      else { ctx.rect(bx, by, box.w, box.h); }
      ctx.fill();
      // A hairline in the node's own colour: the label is then tied to its shape even when both
      // are small, which is what makes a screenshot readable without interaction.
      ctx.strokeStyle = isHub ? self.colorFor(node.label) : "rgba(148,163,184,0.35)";
      ctx.lineWidth = isHub ? 1.4 : 1;
      ctx.stroke();
      ctx.fillStyle = isHub ? "#ffffff" : "#e8eef7";
      ctx.textAlign = "left";
      ctx.fillText(text, bx + pad, by + boxHeight - (isHub ? 7 : 6));
      ctx.globalAlpha = 1;
    });

    this._renderEdgeNames(ctx, focused, route, placed);
    if (this.options.legend) { this._renderLegend(ctx, width, height); }
  };

  /* The legend, built from what is on screen.
   *
   * A key that lists every label the schema can produce is a key nobody reads past the third row.
   * This one lists only the labels actually drawn, in the order of how many nodes each one has, so
   * it answers "what am I looking at" for this picture and not for the database. */
  GraphView.prototype._renderLegend = function (ctx, width, height) {
    var tally = {};
    var self = this;
    this.nodes.forEach(function (node) {
      if (!node._visible) { return; }
      tally[node.label] = (tally[node.label] || 0) + 1;
    });
    var rows = Object.keys(tally).sort(function (a, b) { return tally[b] - tally[a]; });
    if (!rows.length) { return; }

    var rowHeight = 25;
    var padding = 16;
    var boxHeight = rows.length * rowHeight + padding * 2 + 22;
    var boxWidth = 258;
    var x = 18;
    var y = height - boxHeight - 18;

    ctx.globalAlpha = 0.92;
    ctx.fillStyle = "rgba(2,6,23,0.86)";
    ctx.beginPath();
    if (ctx.roundRect) { ctx.roundRect(x, y, boxWidth, boxHeight, 10); } else { ctx.rect(x, y, boxWidth, boxHeight); }
    ctx.fill();
    ctx.strokeStyle = "rgba(100,116,139,0.4)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.fillStyle = "#94a3b8";
    ctx.font = "bold 11.5px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "left";
    ctx.fillText("QUÉ ESTÁS VIENDO", x + padding, y + padding + 8);
    rows.forEach(function (label, index) {
      var cy = y + padding + 24 + index * rowHeight + rowHeight / 2;
      ctx.fillStyle = self.colorFor(label);
      drawShape(ctx, x + padding + 7, cy, 7, shapeFor(label));
      ctx.fillStyle = "#e8eef7";
      ctx.font = "14.5px ui-sans-serif, system-ui, sans-serif";
      var name = LABELS_ES[label] || label;
      ctx.fillText(name, x + padding + 24, cy + 4.5);
      ctx.fillStyle = "#94a3b8";
      ctx.textAlign = "right";
      ctx.fillText(String(tally[label]), x + boxWidth - padding, cy + 4.5);
      ctx.textAlign = "left";
    });
    ctx.globalAlpha = 1;
  };

  /* The predicates, on the edges where they can be read.
   *
   * Showing twelve relation names at once is noise. The rule: name the edges that touch the
   * selection, name the edges on the route, name the hovered edge — and once the view is
   * zoomed in far enough that the text is bigger than the gap between nodes, name the rest.
   * A graph that never says `COMPARTE_DOMICILIO` is a constellation; one that says it twelve
   * times is a table with extra steps. */
  GraphView.prototype._renderEdgeNames = function (ctx, focused, route, placed) {
    var self = this;
    var wide = this.mode === "3d" ? this.camera.dist < 520 : this.scale > 2.6;
    var shown = 0;

    this.edges.forEach(function (edge) {
      if (!edge.source._visible || !edge.target._visible) { return; }
      if (self.isHidden(edge.source.label) || self.isHidden(edge.target.label)) { return; }
      var touchesSelection = focused && (
        edge.source.id === self.selected || edge.target.id === self.selected ||
        self.pinned.indexOf(edge.source.id) >= 0 || self.pinned.indexOf(edge.target.id) >= 0
      );
      var onRoute = route[edge.source.id] && route[edge.target.id] &&
        Math.abs(route[edge.source.id] - route[edge.target.id]) === 1;
      var hovered = edge === self.hoveredEdge;
      if (!(touchesSelection || onRoute || hovered || wide)) { return; }
      if (!hovered && shown > 22) { return; }
      shown += 1;

      var mx = (edge.source._sx + edge.target._sx) / 2;
      var my = (edge.source._sy + edge.target._sy) / 2;
      var label = String(edge.rel || "");
      if (!label) { return; }
      ctx.font = (onRoute ? "bold 14px " : "bold 10px ") + "ui-monospace, SFMono-Regular, Menlo, monospace";
      var width = ctx.measureText(label).width;
      var pad = onRoute ? 6 : 4.5;
      // Readable text lives in the same rectangle registry as the node labels: a relation name
      // written over a node's name is worse than a relation name not written.
      var boxHeight = onRoute ? 18 : 15;
      var box = { x: mx - width / 2 - pad, y: my - boxHeight / 2, w: width + pad * 2, h: boxHeight };
      var mustShow = hovered || onRoute;
      if (!mustShow) {
        var clash = false;
        for (var index = 0; index < placed.length; index += 1) {
          var other = placed[index];
          if (box.x < other.x + other.w && box.x + box.w > other.x
            && box.y < other.y + other.h && box.y + box.h > other.y) { clash = true; break; }
        }
        if (clash) { return; }
      }
      placed.push(box);
      var alpha = (hovered || onRoute ? 1 : touchesSelection ? 0.94 : 0.62) * Math.min(edge.source._alpha, edge.target._alpha);
      ctx.globalAlpha = clamp(alpha, 0, 1);
      ctx.fillStyle = onRoute ? "rgba(8,47,73,0.95)" : "rgba(2,6,23,0.88)";
      ctx.beginPath();
      if (ctx.roundRect) { ctx.roundRect(mx - width / 2 - pad, my - 7, width + pad * 2, 14, 3); }
      else { ctx.rect(mx - width / 2 - pad, my - 7, width + pad * 2, 14); }
      ctx.fill();
      ctx.strokeStyle = onRoute ? "rgba(56,189,248,0.9)" : "rgba(100,116,139,0.55)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = onRoute ? "#bae6fd" : "#cbd5e1";
      ctx.textAlign = "center";
      ctx.fillText(label, mx, my + 3.4);
      ctx.globalAlpha = 1;
    });
    ctx.textAlign = "left";
  };

  GraphView.prototype.labels = function () {
    var seen = {};
    return this.nodes.filter(function (node) {
      if (seen[node.label]) { return false; }
      seen[node.label] = true;
      return true;
    }).map(function (node) {
      return { label: node.label, color: this.colorFor(node.label) };
    }, this);
  };

  //: The two-click path query needs to remember the first node picked.
  GraphView.prototype.pin = function (nodeId) {
    var at = this.pinned.indexOf(nodeId);
    if (at >= 0) {
      this.pinned.splice(at, 1);
    } else {
      this.pinned.push(nodeId);
      while (this.pinned.length > 2) { this.pinned.shift(); }
    }
    this.render();
    return this.pinned.slice();
  };

  //: Light a set of nodes until the next call. Used by the step waterfall on hover.
  GraphView.prototype.setPeek = function (ids) {
    this.peek = (ids || []).slice();
    this.render();
    return this.peek.length;
  };

  GraphView.prototype.clearPins = function () {
    this.pinned = [];
    this.path = null;
    this.render();
  };

  global.GraphView = GraphView;
  global.GraphRelNames = REL_ES;
})(window);
