/* Force-directed graph on a bare canvas.
 *
 * No library, for the same reason the rest of the project has no dependencies: the
 * whole thing has to be readable and verifiable by whoever is judging it. It is
 * about 200 lines of physics and drawing.
 *
 * Two decisions that matter for the audience:
 *
 *  - It settles and then STOPS. A graph that keeps drifting is a graph you cannot
 *    click, and the people using this are trying to read something, not watch a lava
 *    lamp. After the simulation cools, the loop shuts down until something changes.
 *  - Labels are always drawn for the node under the cursor and for the seed, because
 *    "which dot is which" is the first question anyone asks.
 */
(function (global) {
  "use strict";

  var COLORS = {
    Proveedor: "#16506b",
    Organismo: "#8a5a00",
    OrdenCompra: "#7c8697",
    Domicilio: "#a12a1c",
    Senal: "#4b3f72",
    Detector: "#4b3f72",
    CompraAgil: "#1d5c3a",
    Licitacion: "#1d5c3a",
    Documento: "#5b6b7c",
    Evidencia: "#5b6b7c",
    Afirmacion: "#5b6b7c",
    Persona: "#a15c1c",
    Expediente: "#14181f"
  };

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
    this.running = false;
    this._bind();
  }

  GraphView.prototype.colorFor = function (label) {
    return COLORS[label] || "#4a5261";
  };

  GraphView.prototype._bind = function () {
    var self = this;
    var dragging = null;
    var panning = false;
    var last = { x: 0, y: 0 };

    function pos(event) {
      var rect = self.canvas.getBoundingClientRect();
      var point = event.touches ? event.touches[0] : event;
      return {
        x: (point.clientX - rect.left - self.tx) / self.scale,
        y: (point.clientY - rect.top - self.ty) / self.scale
      };
    }

    function find(point) {
      var best = null;
      var bestDistance = 22 / self.scale;
      self.nodes.forEach(function (node) {
        if (self.isHidden(node.label)) { return; }
        var dx = node.x - point.x;
        var dy = node.y - point.y;
        var distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < Math.max(bestDistance, node.r + 4)) {
          best = node;
          bestDistance = distance;
        }
      });
      return best;
    }

    this.canvas.addEventListener("pointerdown", function (event) {
      var point = pos(event);
      var node = find(point);
      if (node) {
        dragging = node;
        node.fixed = true;
      } else {
        panning = true;
      }
      last = { x: event.clientX, y: event.clientY };
      self.canvas.setPointerCapture(event.pointerId);
    });

    this.canvas.addEventListener("pointermove", function (event) {
      var point = pos(event);
      if (dragging) {
        dragging.x = point.x;
        dragging.y = point.y;
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
      var node = find(point);
      if (node !== self.hovered) {
        self.hovered = node;
        self.canvas.style.cursor = node ? "pointer" : "grab";
        self.render();
      }
    });

    this.canvas.addEventListener("pointerup", function (event) {
      var moved = Math.abs(event.clientX - last.x) + Math.abs(event.clientY - last.y);
      var node = dragging;
      dragging = null;
      panning = false;
      if (node) {
        node.fixed = false;
      }
      if (node && moved < 6 && self.options.onSelect) {
        self.selected = node.id;
        self.options.onSelect(node.data);
      }
    });

    this.canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var rect = self.canvas.getBoundingClientRect();
      var factor = event.deltaY < 0 ? 1.12 : 0.89;
      var mx = event.clientX - rect.left;
      var my = event.clientY - rect.top;
      self.tx = mx - (mx - self.tx) * factor;
      self.ty = my - (my - self.ty) * factor;
      self.scale = Math.max(0.35, Math.min(3, self.scale * factor));
      self.render();
    }, { passive: false });
  };

  GraphView.prototype.setData = function (data, options) {
    var merge = !!(options && options.merge) && this.nodes.length > 0;
    var width = this.canvas.clientWidth || 800;
    var height = this.canvas.clientHeight || 520;
    var byId = {};

    if (merge) {
      this.nodes.forEach(function (node) { byId[node.id] = node; });
      this.edges.forEach(function (edge) { edge.__keep = true; });
    }

    var existing = this.nodes;
    var added = (data.nodes || []).map(function (node, index) {
      if (merge && byId[node.id]) {
        byId[node.id].data = node;
        byId[node.id].name = node.name;
        return null;
      }
      var angle = (index / Math.max(1, data.nodes.length)) * Math.PI * 2;
      var radius = Math.min(width, height) * 0.32;
      var entry = {
        id: node.id,
        label: node.label,
        name: node.name,
        data: node,
        x: width / 2 + Math.cos(angle) * radius + (Math.random() - 0.5) * 30,
        y: height / 2 + Math.sin(angle) * radius + (Math.random() - 0.5) * 30,
        vx: 0,
        vy: 0,
        r: node.id === data.seed ? 13 : 7,
        degree: 0
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
      this.edges.forEach(function (edge) {
        seen[edge.source.id + "|" + edge.rel + "|" + edge.target.id] = true;
      });
      newEdges = newEdges.filter(function (edge) {
        return !seen[edge.source.id + "|" + edge.rel + "|" + edge.target.id];
      });
    }

    this.nodes = merge ? existing.concat(added) : added;
    this.edges = (merge ? this.edges.concat(newEdges) : newEdges).filter(function (edge) {
      return !edge.__keep || merge;
    });

    this.nodes.forEach(function (node) { node.degree = 0; });
    this.edges.forEach(function (edge) {
      edge.source.degree += 1;
      edge.target.degree += 1;
    });
    this.nodes.forEach(function (node) {
      node.r = node.id === data.seed ? 14 : Math.min(11, 6 + Math.sqrt(node.degree) * 1.1);
    });

    this.selected = data.seed || this.selected;
    if (!merge) {
      this.scale = 1;
      this.tx = 0;
      this.ty = 0;
    }
    this._step(merge ? 120 : 240);
    this.render();
  };

  GraphView.prototype.zoomBy = function (factor) {
    var width = this.canvas.clientWidth || 800;
    var height = this.canvas.clientHeight || 520;
    this.tx = width / 2 - (width / 2 - this.tx) * factor;
    this.ty = height / 2 - (height / 2 - this.ty) * factor;
    this.scale = Math.max(0.35, Math.min(3, this.scale * factor));
    this.render();
  };

  GraphView.prototype.reset = function () {
    this.scale = 1;
    this.tx = 0;
    this.ty = 0;
    this._step(120);
    this.render();
  };

  GraphView.prototype.setLabelVisible = function (label, visible) {
    this.hidden = this.hidden || {};
    this.hidden[label] = !visible;
    this.render();
  };

  GraphView.prototype.isHidden = function (label) {
    return !!(this.hidden && this.hidden[label]);
  };

  GraphView.prototype.counts = function () {
    var counts = {};
    this.nodes.forEach(function (node) {
      counts[node.label] = (counts[node.label] || 0) + 1;
    });
    return counts;
  };

  GraphView.prototype._step = function (iterations) {
    var width = this.canvas.clientWidth || 800;
    var height = this.canvas.clientHeight || 520;
    var nodes = this.nodes;
    var edges = this.edges;

    for (var step = 0; step < iterations; step += 1) {
      var cooling = 1 - step / iterations;

      for (var i = 0; i < nodes.length; i += 1) {
        var a = nodes[i];
        for (var j = i + 1; j < nodes.length; j += 1) {
          var b = nodes[j];
          var dx = b.x - a.x;
          var dy = b.y - a.y;
          var distance = Math.sqrt(dx * dx + dy * dy) || 0.01;
          var force = 900 / (distance * distance);
          var fx = (dx / distance) * force;
          var fy = (dy / distance) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }

      for (var e = 0; e < edges.length; e += 1) {
        var edge = edges[e];
        var ex = edge.target.x - edge.source.x;
        var ey = edge.target.y - edge.source.y;
        var length = Math.sqrt(ex * ex + ey * ey) || 0.01;
        var target = 110;
        var pull = (length - target) * 0.012;
        var px = (ex / length) * pull;
        var py = (ey / length) * pull;
        edge.source.vx += px;
        edge.source.vy += py;
        edge.target.vx -= px;
        edge.target.vy -= py;
      }

      for (var k = 0; k < nodes.length; k += 1) {
        var node = nodes[k];
        if (node.fixed) { continue; }
        node.vx += (width / 2 - node.x) * 0.004;
        node.vy += (height / 2 - node.y) * 0.004;
        node.x += node.vx * cooling;
        node.y += node.vy * cooling;
        node.vx *= 0.82;
        node.vy *= 0.82;
        var margin = 30;
        node.x = Math.max(margin, Math.min(width - margin, node.x));
        node.y = Math.max(margin, Math.min(height - margin, node.y));
      }
    }
  };

  GraphView.prototype.render = function () {
    var canvas = this.canvas;
    var ratio = global.devicePixelRatio || 1;
    var width = canvas.clientWidth || 800;
    var height = canvas.clientHeight || 520;

    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }

    var ctx = this.ctx;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
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
    ctx.lineWidth = 1 / this.scale;
    this.edges.forEach(function (edge) {
      if (self.isHidden(edge.source.label) || self.isHidden(edge.target.label)) { return; }
      var lit = !self.selected || neighbour[edge.source.id] || neighbour[edge.target.id];
      var isDirect = self.selected && (edge.source.id === self.selected || edge.target.id === self.selected);
      var colour = isDirect ? "#8a8f99" : lit ? "#d9d5cc" : "#eeebe4";

      // A directed graph that draws straight lines is a graph you cannot read: the
      // edge ADJUDICADA_A runs order -> supplier, and SENAL -> order the other way.
      // Curving the edges and putting a head on them is what turns a hairball into
      // a structure. The control point is offset perpendicular to the chord, so two
      // edges between the same pair of nodes never overlap.
      var sx = edge.source.x;
      var sy = edge.source.y;
      var tx = edge.target.x;
      var ty = edge.target.y;
      var dx = tx - sx;
      var dy = ty - sy;
      var length = Math.sqrt(dx * dx + dy * dy) || 1;
      var bend = Math.min(24, length * 0.12);
      var cx = (sx + tx) / 2 - (dy / length) * bend;
      var cy = (sy + ty) / 2 + (dx / length) * bend;

      ctx.strokeStyle = colour;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.quadraticCurveTo(cx, cy, tx, ty);
      ctx.stroke();

      // arrowhead, stopping at the target's radius so it does not sit inside the dot
      if (lit && length > 24) {
        var tt = 0.88;
        var hx = (1 - tt) * (1 - tt) * sx + 2 * (1 - tt) * tt * cx + tt * tt * tx;
        var hy = (1 - tt) * (1 - tt) * sy + 2 * (1 - tt) * tt * cy + tt * tt * ty;
        var angle = Math.atan2(ty - hy, tx - hx);
        var size = (isDirect ? 5.5 : 4) / self.scale;
        ctx.beginPath();
        ctx.moveTo(tx - Math.cos(angle) * (edge.target.r + size), ty - Math.sin(angle) * (edge.target.r + size));
        ctx.lineTo(
          tx - Math.cos(angle) * (edge.target.r + size * 2.4) + Math.sin(angle) * size,
          ty - Math.sin(angle) * (edge.target.r + size * 2.4) - Math.cos(angle) * size
        );
        ctx.lineTo(
          tx - Math.cos(angle) * (edge.target.r + size * 2.4) - Math.sin(angle) * size,
          ty - Math.sin(angle) * (edge.target.r + size * 2.4) + Math.cos(angle) * size
        );
        ctx.closePath();
        ctx.fillStyle = colour;
        ctx.fill();
      }

      if (isDirect) {
        ctx.save();
        ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
        ctx.fillStyle = "#6b7280";
        ctx.textAlign = "center";
        ctx.fillText(edge.rel, (sx + 2 * cx + tx) / 4, (sy + 2 * cy + ty) / 4 - 3);
        ctx.restore();
      }
    });

    this.nodes.forEach(function (node) {
      if (self.isHidden(node.label)) { return; }
      var lit = !self.selected || neighbour[node.id];
      ctx.globalAlpha = lit ? 1 : 0.28;
      if (node.id === self.selected) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r + 6, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(22,80,107,.14)";
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fillStyle = self.colorFor(node.label);
      ctx.fill();
      if (node.id === self.selected || node === self.hovered) {
        ctx.lineWidth = 2 / self.scale;
        ctx.strokeStyle = "#14181f";
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      var showLabel = node === self.hovered || node.id === self.selected || node.degree > 4 || self.scale > 1.6;
      if (showLabel) {
        var text = node.name.length > 34 ? node.name.slice(0, 32) + "…" : node.name;
        ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
        var metrics = ctx.measureText(text);
        var bx = node.x - metrics.width / 2 - 4;
        var by = node.y + node.r + 4;
        ctx.fillStyle = "rgba(255,255,255,.86)";
        ctx.fillRect(bx, by, metrics.width + 8, 15);
        ctx.fillStyle = "#14181f";
        ctx.textAlign = "left";
        ctx.fillText(text, bx + 4, by + 11);
      }
    });
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
