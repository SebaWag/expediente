/* The graph canvas: an organic layout, a camera, and a glow.
 *
 * Three things make this read as a living structure instead of a diagram:
 *
 *  1. The initial layout is seeded into a brain silhouette — two lobes, a fissure —
 *     and the physics keeps the cloud inside it. Signals cluster in one lobe, orders
 *     and documents in the other, detectors along the middle. It is a real map of the
 *     system's parts, drawn as a shape a person recognises.
 *  2. Depth: nodes carry a glow, edges are drawn with low alpha and a gradient, and
 *     the whole field breathes with a slow drift. Nothing spins.
 *  3. The camera. `flyTo` eases the view to a node and zooms in; the node pulses so the
 *     eye finds it. A graph you can only scroll is a graph you cannot navigate.
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

  //: Where each kind of node sits in the layered layout. Detectors in the core, the
  //: signals they produced in the first ring, the orders those signals observe in the
  //: second, the documents that accredit them on the outside. A free force layout over
  //: this data draws a hairball: edges cross the middle, nothing reads as structure, and
  //: the two nodes that matter most disappear among 440 others. Rings fix that — the
  //: picture now says what the system does, in layers.
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

  function radialPoint(index, total, radius, width, height, random) {
    var angle = (index / Math.max(1, total)) * Math.PI * 2 + (random() - 0.5) * 0.05;
    var spread = radius * Math.min(width, height) * 0.46;
    return {
      x: width / 2 + Math.cos(angle) * spread * 1.18,
      y: height / 2 + Math.sin(angle) * spread * 0.86
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
      return { x: x, y: y };
    }
    return { x: width / 2, y: height / 2 };
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
    this.selected = null;
    this.hovered = null;
    this.hidden = {};
    this.pulse = null;
    this._fly = null;
    this._time = 0;
    this._raf = null;
    this._bind();
  }

  GraphView.prototype.colorFor = function (label) {
    return COLORS[label] || "#94a3b8";
  };

  GraphView.prototype._bind = function () {
    var self = this;
    var dragging = null;
    var panning = false;
    var last = { x: 0, y: 0 };
    var moved = 0;

    function point(event) {
      var rect = self.canvas.getBoundingClientRect();
      var source = event.touches ? event.touches[0] : event;
      return {
        x: (source.clientX - rect.left - self.tx) / self.scale,
        y: (source.clientY - rect.top - self.ty) / self.scale
      };
    }

    function find(at) {
      var best = null;
      var bestDistance = 26 / self.scale;
      for (var i = 0; i < self.nodes.length; i += 1) {
        var node = self.nodes[i];
        if (self.isHidden(node.label)) { continue; }
        var dx = node.x - at.x;
        var dy = node.y - at.y;
        var distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < Math.max(bestDistance, node.r + 5) && (!best || distance < bestDistance)) {
          best = node;
          bestDistance = distance;
        }
      }
      return best;
    }

    this.canvas.addEventListener("pointerdown", function (event) {
      var at = point(event);
      var node = find(at);
      moved = 0;
      if (node) { dragging = node; node.fixed = true; } else { panning = true; }
      last = { x: event.clientX, y: event.clientY };
      try { self.canvas.setPointerCapture(event.pointerId); } catch (error) { /* older browsers */ }
    });

    this.canvas.addEventListener("pointermove", function (event) {
      var at = point(event);
      moved += Math.abs(event.clientX - last.x) + Math.abs(event.clientY - last.y);
      if (dragging) {
        dragging.x = at.x;
        dragging.y = at.y;
        self.render();
        return;
      }
      if (panning) {
        self.tx += event.clientX - last.x;
        self.ty += event.clientY - last.y;
        last = { x: event.clientX, y: event.clientY };
        self.render();
        return;
      }
      var node = find(at);
      if (node !== self.hovered) {
        self.hovered = node;
        self.canvas.style.cursor = node ? "pointer" : "grab";
        self.render();
      }
    });

    this.canvas.addEventListener("pointerup", function (event) {
      var node = dragging;
      dragging = null;
      panning = false;
      if (node) { node.fixed = false; }
      if (node && moved < 8 && self.options.onSelect) {
        self.selected = node.id;
        self.options.onSelect(node.data);
        self.render();
      }
    });

    this.canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var rect = self.canvas.getBoundingClientRect();
      var factor = event.deltaY < 0 ? 1.12 : 0.89;
      var mx = event.clientX - rect.left;
      var my = event.clientY - rect.top;
      self._fly = null;
      self.tx = mx - (mx - self.tx) * factor;
      self.ty = my - (my - self.ty) * factor;
      self.scale = Math.max(0.25, Math.min(4, self.scale * factor));
      self.render();
    }, { passive: false });
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
      if (radial) {
        var position = seenByLabel[raw.label] || 0;
        seenByLabel[raw.label] = position + 1;
        spot = radialPoint(position, byLabel[raw.label], RING[raw.label] !== undefined ? RING[raw.label] : 0.5, width, height, Math.random);
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
      var seen = {};
      this.edges.forEach(function (edge) { seen[edge.source.id + "|" + edge.target.id] = true; });
      newEdges = newEdges.filter(function (edge) {
        return !seen[edge.source.id + "|" + edge.target.id];
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
    }
    // A radial layout is already the answer: relaxing it would pull it back toward a
    // hairball. Only the merged neighbourhoods get the force pass.
    this._settle(merge ? 90 : radial ? 0 : 220);
    this.render();
    this._animate();
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
          if (d2 > 40000) { continue; }
          var force = 700 / d2;
          var d = Math.sqrt(d2);
          a.vx -= (dx / d) * force;
          a.vy -= (dy / d) * force;
          b.vx += (dx / d) * force;
          b.vy += (dy / d) * force;
        }
      }
      for (var e = 0; e < edges.length; e += 1) {
        var edge = edges[e];
        var ex = edge.target.x - edge.source.x;
        var ey = edge.target.y - edge.source.y;
        var length = Math.sqrt(ex * ex + ey * ey) || 0.01;
        var pull = (length - 70) * 0.008;
        edge.source.vx += (ex / length) * pull;
        edge.source.vy += (ey / length) * pull;
        edge.target.vx -= (ex / length) * pull;
        edge.target.vy -= (ey / length) * pull;
      }
      for (var k = 0; k < nodes.length; k += 1) {
        var node = nodes[k];
        if (node.fixed) { continue; }
        // a soft pull toward the middle keeps the silhouette
        node.vx += (cx - node.x) * 0.0025;
        node.vy += (cy - node.y) * 0.0025;
        node.x += node.vx * cooling;
        node.y += node.vy * cooling;
        node.vx *= 0.84;
        node.vy *= 0.84;
        var margin = 24;
        node.x = Math.max(margin, Math.min(width - margin, node.x));
        node.y = Math.max(margin, Math.min(height - margin, node.y));
      }
    }
  };

  GraphView.prototype.flyTo = function (nodeId, zoom) {
    var node = this.nodes.filter(function (candidate) { return candidate.id === nodeId; })[0];
    if (!node) { return false; }
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    var targetScale = zoom || 1.9;
    this._fly = {
      fromScale: this.scale,
      toScale: targetScale,
      fromX: this.tx,
      fromY: this.ty,
      toX: width / 2 - node.x * targetScale,
      toY: height / 2 - node.y * targetScale,
      start: performance.now(),
      duration: 900
    };
    this.selected = nodeId;
    this.pulse = { id: nodeId, start: performance.now() };
    this._animate();
    return true;
  };

  GraphView.prototype._animate = function () {
    var self = this;
    if (this._raf) { return; }
    function tick(now) {
      var keepGoing = false;
      if (self._fly) {
        var t = Math.min(1, (now - self._fly.start) / self._fly.duration);
        var ease = 1 - Math.pow(1 - t, 3);
        self.scale = self._fly.fromScale + (self._fly.toScale - self._fly.fromScale) * ease;
        self.tx = self._fly.fromX + (self._fly.toX - self._fly.fromX) * ease;
        self.ty = self._fly.fromY + (self._fly.toY - self._fly.fromY) * ease;
        if (t >= 1) { self._fly = null; }
        keepGoing = true;
      }
      if (self.pulse) {
        if (now - self.pulse.start > 1600) { self.pulse = null; }
        else { keepGoing = true; }
      }
      self._time = now;
      self.render();
      if (keepGoing) {
        self._raf = requestAnimationFrame(tick);
      } else {
        self._raf = null;
      }
    }
    this._raf = requestAnimationFrame(tick);
  };

  GraphView.prototype.setLabelVisible = function (label, visible) {
    this.hidden[label] = !visible;
    this.render();
  };

  GraphView.prototype.isHidden = function (label) {
    return !!this.hidden[label];
  };

  GraphView.prototype.counts = function () {
    var counts = {};
    this.nodes.forEach(function (node) { counts[node.label] = (counts[node.label] || 0) + 1; });
    return counts;
  };

  GraphView.prototype.zoomBy = function (factor) {
    var width = this.canvas.clientWidth || 900;
    var height = this.canvas.clientHeight || 700;
    this.tx = width / 2 - (width / 2 - this.tx) * factor;
    this.ty = height / 2 - (height / 2 - this.ty) * factor;
    this.scale = Math.max(0.25, Math.min(4, this.scale * factor));
    this.render();
  };

  GraphView.prototype.reset = function () {
    this.scale = 1;
    this.tx = 0;
    this.ty = 0;
    this.selected = null;
    this._settle(80);
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

    // the field: a soft radial wash so the brain sits in something, not on nothing
    var wash = ctx.createRadialGradient(width / 2, height / 2, 40, width / 2, height / 2, Math.max(width, height) * 0.62);
    wash.addColorStop(0, "rgba(56,189,248,0.07)");
    wash.addColorStop(1, "rgba(2,6,23,0)");
    ctx.fillStyle = wash;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.translate(this.tx, this.ty);
    ctx.scale(this.scale, this.scale);

    var neighbour = {};
    if (this.selected) {
      neighbour[this.selected] = true;
      this.edges.forEach(function (edge) {
        if (edge.source.id === this.selected) { neighbour[edge.target.id] = true; }
        if (edge.target.id === this.selected) { neighbour[edge.source.id] = true; }
      }, this);
    }

    var self = this;
    ctx.lineWidth = 0.8 / this.scale;
    this.edges.forEach(function (edge) {
      if (self.isHidden(edge.source.label) || self.isHidden(edge.target.label)) { return; }
      var direct = self.selected && (edge.source.id === self.selected || edge.target.id === self.selected);
      var lit = !self.selected || neighbour[edge.source.id] || neighbour[edge.target.id];
      var alpha = direct ? 0.75 : lit ? 0.16 : 0.05;
      ctx.strokeStyle = direct
        ? "rgba(148,197,255,0.8)"
        : "rgba(148,163,184," + alpha + ")";
      var mx = (edge.source.x + edge.target.x) / 2;
      var my = (edge.source.y + edge.target.y) / 2;
      ctx.beginPath();
      ctx.moveTo(edge.source.x, edge.source.y);
      ctx.quadraticCurveTo(mx, my, edge.target.x, edge.target.y);
      ctx.stroke();
    });

    var drift = Math.sin(this._time / 2600) * 0.6;
    this.nodes.forEach(function (node) {
      if (self.isHidden(node.label)) { return; }
      var lit = !self.selected || neighbour[node.id];
      var colour = self.colorFor(node.label);
      var radius = node.r + (node === self.hovered ? 2 : 0);

      ctx.globalAlpha = lit ? 1 : 0.22;

      // glow
      var glow = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, radius * 3.2);
      glow.addColorStop(0, colour);
      glow.addColorStop(0.35, colour);
      glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = (lit ? 0.30 : 0.07);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius * 3.2 + drift, 0, Math.PI * 2);
      ctx.fill();

      ctx.globalAlpha = lit ? 1 : 0.25;
      ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.fill();

      if (node.label === "Detector") {
        // A solid ring, not another glow: the two nodes that generated every case in
        // the graph have to survive a screen full of them.
        ctx.globalAlpha = 0.85;
        ctx.strokeStyle = colour;
        ctx.lineWidth = 2 / self.scale;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 7, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (node.id === self.selected) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.6 / self.scale;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + (node.label === "Detector" ? 13 : 5), 0, Math.PI * 2);
        ctx.stroke();
      }

      // the pulse after a fly-to: a ring that expands once and fades
      if (self.pulse && self.pulse.id === node.id) {
        var age = (performance.now() - self.pulse.start) / 1600;
        ctx.globalAlpha = Math.max(0, 1 - age);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2 / self.scale;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 8 + age * 46, 0, Math.PI * 2);
        ctx.stroke();
      }

      var showLabel = node === self.hovered || node.id === self.selected
        || node.label === "Detector" || (self.scale > 2.4 && node.degree > 3);
      if (showLabel) {
        var text = String(node.name);
        var isHub = node.label === "Detector";
        if (text.length > 42) { text = text.slice(0, 40) + "…"; }
        // The detector labels are the ones a stranger reads first, so they get their own
        // size and a solid pill instead of a translucent one. The first visual review
        // called them "small and without contrast", which is the worst thing you can say
        // about the two labels that explain what the whole screen is.
        ctx.font = isHub
          ? "bold 13px ui-sans-serif, system-ui, sans-serif"
          : "11px ui-sans-serif, system-ui, sans-serif";
        var metrics = ctx.measureText(text);
        var pad = isHub ? 8 : 5;
        var height = isHub ? 21 : 17;
        var bx = node.x - metrics.width / 2 - pad;
        var by = node.y + radius + (isHub ? 12 : 6);
        ctx.globalAlpha = 1;
        ctx.fillStyle = isHub ? "rgba(8,15,30,0.96)" : "rgba(2,6,23,0.82)";
        ctx.beginPath();
        if (ctx.roundRect) { ctx.roundRect(bx, by, metrics.width + pad * 2, height, isHub ? 6 : 4); }
        else { ctx.rect(bx, by, metrics.width + pad * 2, height); }
        ctx.fill();
        if (isHub) {
          ctx.strokeStyle = colour;
          ctx.lineWidth = 1.2 / self.scale;
          ctx.stroke();
        }
        ctx.fillStyle = isHub ? "#ffffff" : "#e2e8f0";
        ctx.textAlign = "left";
        ctx.fillText(text, bx + pad, by + (isHub ? 15 : 12));
      }
      ctx.globalAlpha = 1;
    });

    ctx.restore();
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

  global.GraphView = GraphView;
})(window);
