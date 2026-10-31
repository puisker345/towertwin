// 3D Order-Book Terrain Mesh (Three.js) + 2D Fallback Canvas (Section 10.4)

const COLS = 81; // -40 to +40 ticks around mid
const ROWS = 120; // scrolling history rows (row 0 = newest at front)
const HALF_COLS = 40;

export class TwinVisualizer {
  constructor(canvas3d, canvas2d, viewportEl) {
    this.canvas3d = canvas3d;
    this.canvas2d = canvas2d;
    this.viewportEl = viewportEl;
    this.mode = "3d"; // "3d" | "2d"
    this.webglAvailable = false;

    // Historical ring buffer of rows (length ROWS)
    // Each row: { mid, heights: Float32Array(COLS), crash: boolean }
    this.history = [];
    for (let r = 0; r < ROWS; r++) {
      const heights = new Float32Array(COLS);
      for (let c = 0; c < COLS; c++) {
        const dist = Math.abs(c - HALF_COLS);
        heights[c] = dist === 0 ? 0.2 : 14.0 * Math.exp(-0.11 * dist);
      }
      this.history.push({ mid: 10000, heights, crash: false });
    }

    this.latestTick = {
      mid: 10000,
      quote: { bid_price: 9998, bid_size: 8, ask_price: 10002, ask_size: 8 },
      halted: false,
      killed: false,
      crash_active: false,
      depth: { bids: [], asks: [] },
    };

    this.midHistory2D = Array(160).fill(10000);
    this.ripples = []; // { xIdx, zRow, radius, opacity, color }
    this.crashShakeUntilMs = 0;
    this.wasCrashActive = false;
    this.quoteOpacity = 1.0;

    this._initThree();
    this._initResizeObserver();
    this._animate = this._animate.bind(this);
    requestAnimationFrame(this._animate);
  }

  _initThree() {
    if (typeof window.THREE === "undefined") {
      this.setMode("2d");
      return;
    }
    const THREE = window.THREE;
    try {
      this.renderer = new THREE.WebGLRenderer({
        canvas: this.canvas3d,
        antialias: true,
        preserveDrawingBuffer: true,
        powerPreference: "high-performance",
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.setClearColor(0x070b12, 1);

      this.scene = new THREE.Scene();
      this.scene.fog = new THREE.FogExp2(0x070b12, 0.0085);

      this.baseCameraPos = new THREE.Vector3(0, 34, 48);
      this.baseLookAt = new THREE.Vector3(0, 2, -16);
      this.camera = new THREE.PerspectiveCamera(44, 16 / 9, 0.5, 260);
      this.camera.position.copy(this.baseCameraPos);
      this.camera.lookAt(this.baseLookAt);

      // Lighting
      const ambient = new THREE.AmbientLight(0xdbeafe, 0.75);
      this.scene.add(ambient);

      const dirLight = new THREE.DirectionalLight(0xffffff, 0.85);
      dirLight.position.set(18, 50, 32);
      this.scene.add(dirLight);

      // Terrain BufferGeometry: COLS x ROWS grid
      // x spans [-36, +36], z spans [22 (front/newest) to -58 (back/oldest)]
      this.terrainGeo = new THREE.BufferGeometry();
      const vertexCount = COLS * ROWS;
      this.positions = new Float32Array(vertexCount * 3);
      this.colors = new Float32Array(vertexCount * 3);

      for (let r = 0; r < ROWS; r++) {
        const z = 22.0 - (r / (ROWS - 1)) * 80.0;
        for (let c = 0; c < COLS; c++) {
          const x = ((c - HALF_COLS) / HALF_COLS) * 34.0;
          const idx = (r * COLS + c) * 3;
          this.positions[idx] = x;
          this.positions[idx + 1] = this.history[r].heights[c] * 0.65;
          this.positions[idx + 2] = z;
        }
      }

      const indices = [];
      for (let r = 0; r < ROWS - 1; r++) {
        for (let c = 0; c < COLS - 1; c++) {
          const a = r * COLS + c;
          const b = r * COLS + (c + 1);
          const d = (r + 1) * COLS + c;
          const e = (r + 1) * COLS + (c + 1);
          indices.push(a, d, b);
          indices.push(b, d, e);
        }
      }

      // Glowing mid line along z at x = 0 (created before _updateTerrainColorsAndHeights)
      const midPoints = [];
      for (let r = 0; r < ROWS; r++) {
        const z = 22.0 - (r / (ROWS - 1)) * 80.0;
        midPoints.push(new THREE.Vector3(0, 1.2, z));
      }
      this.midLineGeo = new THREE.BufferGeometry().setFromPoints(midPoints);
      const midLineMat = new THREE.LineBasicMaterial({
        color: 0x22d3ee,
        linewidth: 2,
      });
      this.midLine = new THREE.Line(this.midLineGeo, midLineMat);

      this.terrainGeo.setIndex(indices);
      this.terrainGeo.setAttribute("position", new THREE.BufferAttribute(this.positions, 3));
      this.terrainGeo.setAttribute("color", new THREE.BufferAttribute(this.colors, 3));
      this._updateTerrainColorsAndHeights();
      this.terrainGeo.computeVertexNormals();

      const terrainMat = new THREE.MeshLambertMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
      });
      this.terrainMesh = new THREE.Mesh(this.terrainGeo, terrainMat);
      this.scene.add(this.terrainMesh);

      // Subtle wireframe overlay for depth perception
      const wireMat = new THREE.MeshBasicMaterial({
        color: 0x1e293b,
        wireframe: true,
        transparent: true,
        opacity: 0.22,
      });
      this.wireMesh = new THREE.Mesh(this.terrainGeo, wireMat);
      this.scene.add(this.wireMesh);
      this.scene.add(this.midLine);

      // Bot quote markers at front row (z = 22)
      const markerGeo = new THREE.CylinderGeometry(0.65, 0.65, 3.2, 18);
      this.bidMarkerMat = new THREE.MeshBasicMaterial({
        color: 0x10b981,
        transparent: true,
        opacity: 0.95,
      });
      this.askMarkerMat = new THREE.MeshBasicMaterial({
        color: 0x34d399,
        transparent: true,
        opacity: 0.95,
      });
      this.bidMarker = new THREE.Mesh(markerGeo, this.bidMarkerMat);
      this.askMarker = new THREE.Mesh(markerGeo, this.askMarkerMat);
      this.bidMarker.position.set(-2.5, 4.0, 22.0);
      this.askMarker.position.set(2.5, 4.0, 22.0);
      this.scene.add(this.bidMarker);
      this.scene.add(this.askMarker);

      // Ripple container for fills
      this.rippleGroup = new THREE.Group();
      this.scene.add(this.rippleGroup);

      this.webglAvailable = true;
      this._resize();
    } catch (_) {
      this.webglAvailable = false;
      this.setMode("2d");
    }
  }

  _initResizeObserver() {
    const onResize = () => this._resize();
    window.addEventListener("resize", onResize);
    if (typeof ResizeObserver !== "undefined" && this.viewportEl) {
      const ro = new ResizeObserver(onResize);
      ro.observe(this.viewportEl);
    }
  }

  _resize() {
    if (!this.viewportEl) return;
    const rect = this.viewportEl.getBoundingClientRect();
    const w = Math.max(300, Math.floor(rect.width));
    const h = Math.max(200, Math.floor(rect.height));

    if (this.webglAvailable && this.renderer && this.camera) {
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
    if (this.canvas2d) {
      this.canvas2d.width = w;
      this.canvas2d.height = h;
    }
  }

  toggleMode() {
    this.setMode(this.mode === "3d" ? "2d" : "3d");
    return this.mode;
  }

  setMode(mode) {
    if (mode === "3d" && !this.webglAvailable) {
      mode = "2d";
    }
    this.mode = mode;
    if (this.mode === "3d") {
      this.canvas3d.classList.remove("hidden");
      this.canvas2d.classList.add("hidden");
    } else {
      this.canvas3d.classList.add("hidden");
      this.canvas2d.classList.remove("hidden");
      this._render2D();
    }
  }

  /**
   * Ingest a live `tick` event from the server SSE stream.
   */
  onTick(evt) {
    this.latestTick = evt;
    const mid = Number(evt.mid || 10000);
    this.midHistory2D.push(mid);
    if (this.midHistory2D.length > 160) {
      this.midHistory2D.shift();
    }

    // Trigger 1s subtle camera shake (max 0.5 deg) when crash begins
    if (evt.crash_active && !this.wasCrashActive) {
      this.crashShakeUntilMs = performance.now() + 1000;
    }
    this.wasCrashActive = Boolean(evt.crash_active);

    // Build newest row heights from evt.depth
    const heights = new Float32Array(COLS);
    const bids = (evt.depth && evt.depth.bids) || [];
    const asks = (evt.depth && evt.depth.asks) || [];

    for (let c = 0; c < COLS; c++) {
      const offset = c - HALF_COLS;
      if (offset === 0) {
        heights[c] = 0.25;
      } else if (offset < 0) {
        const dist = Math.abs(offset);
        const idx = Math.min(bids.length - 1, Math.floor((dist - 1) / 2));
        const base = idx >= 0 && bids[idx] !== undefined ? Number(bids[idx]) : 8.0 * Math.exp(-0.1 * dist);
        heights[c] = base * (1.0 - (dist / 52.0) * 0.35);
      } else {
        const dist = offset;
        const idx = Math.min(asks.length - 1, Math.floor((dist - 1) / 2));
        const base = idx >= 0 && asks[idx] !== undefined ? Number(asks[idx]) : 8.0 * Math.exp(-0.1 * dist);
        heights[c] = base * (1.0 - (dist / 52.0) * 0.35);
      }
    }

    this.history.unshift({
      mid,
      heights,
      crash: Boolean(evt.crash_active),
    });
    if (this.history.length > ROWS) {
      this.history.pop();
    }

    // Spawn expanding ripple rings for each fill
    if (Array.isArray(evt.fills)) {
      for (const f of evt.fills) {
        const priceOffset = Math.max(-HALF_COLS, Math.min(HALF_COLS, Number(f.price) - mid));
        this._spawnFillRipple(priceOffset, f.side);
      }
    }

    if (this.webglAvailable && this.mode === "3d") {
      this._updateTerrainColorsAndHeights();
    } else {
      this._render2D();
    }
  }

  _spawnFillRipple(priceOffsetTicks, side) {
    if (!this.webglAvailable || !window.THREE) return;
    const THREE = window.THREE;
    const x = (priceOffsetTicks / HALF_COLS) * 34.0;
    const ringGeo = new THREE.RingGeometry(0.4, 0.85, 24);
    const color = side === "buy" ? 0x38bdf8 : 0xf59e0b;
    const ringMat = new THREE.MeshBasicMaterial({
      color,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.95,
    });
    const mesh = new THREE.Mesh(ringGeo, ringMat);
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.set(x, 2.2, 22.0);
    this.rippleGroup.add(mesh);
    this.ripples.push({ mesh, mat: ringMat, scale: 1.0, opacity: 0.95 });
  }

  _updateTerrainColorsAndHeights() {
    if (!this.terrainGeo) return;
    const posArr = this.positions;
    const colArr = this.colors;
    const midPosArr = this.midLineGeo.attributes.position.array;

    for (let r = 0; r < ROWS; r++) {
      const row = this.history[r];
      const ageFade = 1.0 - (r / ROWS) * 0.55;
      for (let c = 0; c < COLS; c++) {
        const idx = (r * COLS + c) * 3;
        const h = row.heights[c] * 0.65;
        posArr[idx + 1] = h;

        const offset = c - HALF_COLS;
        const intensity = Math.min(1.0, 0.35 + h / 14.0) * ageFade;

        if (row.crash) {
          // Red tint during crash per SPEC Section 10.4
          colArr[idx] = 0.92 * intensity;
          colArr[idx + 1] = 0.18 * intensity;
          colArr[idx + 2] = 0.22 * intensity;
        } else if (offset < 0) {
          // Cool blue for bid side
          colArr[idx] = 0.08 * intensity;
          colArr[idx + 1] = 0.58 * intensity;
          colArr[idx + 2] = 0.96 * intensity;
        } else if (offset > 0) {
          // Warm amber for ask side
          colArr[idx] = 0.96 * intensity;
          colArr[idx + 1] = 0.62 * intensity;
          colArr[idx + 2] = 0.12 * intensity;
        } else {
          // Bright cyan at mid
          colArr[idx] = 0.15;
          colArr[idx + 1] = 0.85;
          colArr[idx + 2] = 0.95;
        }
      }
      midPosArr[r * 3 + 1] = Math.max(0.9, row.heights[HALF_COLS] * 0.65 + 0.6);
    }

    this.terrainGeo.attributes.position.needsUpdate = true;
    this.terrainGeo.attributes.color.needsUpdate = true;
    this.midLineGeo.attributes.position.needsUpdate = true;
  }

  _animate(nowMs) {
    requestAnimationFrame(this._animate);
    if (this.mode !== "3d" || !this.webglAvailable || !this.renderer) {
      return;
    }

    // Update quote markers & fade out when halted/killed
    const q = this.latestTick.quote;
    const isHaltedOrKilled = Boolean(this.latestTick.halted || this.latestTick.killed || !q);
    const targetOpacity = isHaltedOrKilled ? 0.0 : 0.95;
    this.quoteOpacity += (targetOpacity - this.quoteOpacity) * 0.18;

    this.bidMarkerMat.opacity = this.quoteOpacity;
    this.askMarkerMat.opacity = this.quoteOpacity;
    this.bidMarker.visible = this.quoteOpacity > 0.03 && q && q.bid_price !== null;
    this.askMarker.visible = this.quoteOpacity > 0.03 && q && q.ask_price !== null;

    if (q) {
      const mid = Number(this.latestTick.mid || 10000);
      if (q.bid_price !== null) {
        const bOff = Math.max(-HALF_COLS, Math.min(HALF_COLS, Number(q.bid_price) - mid));
        const bx = (bOff / HALF_COLS) * 34.0;
        const bScale = Math.max(0.6, Math.min(2.6, Number(q.bid_size || 1) / 5.0));
        this.bidMarker.position.set(bx, 2.0 + bScale * 1.4, 22.0);
        this.bidMarker.scale.set(bScale, bScale, bScale);
      }
      if (q.ask_price !== null) {
        const aOff = Math.max(-HALF_COLS, Math.min(HALF_COLS, Number(q.ask_price) - mid));
        const ax = (aOff / HALF_COLS) * 34.0;
        const aScale = Math.max(0.6, Math.min(2.6, Number(q.ask_size || 1) / 5.0));
        this.askMarker.position.set(ax, 2.0 + aScale * 1.4, 22.0);
        this.askMarker.scale.set(aScale, aScale, aScale);
      }
    }

    // Update expanding fill ripples
    for (let i = this.ripples.length - 1; i >= 0; i--) {
      const rp = this.ripples[i];
      rp.scale += 0.22;
      rp.opacity -= 0.035;
      rp.mesh.scale.set(rp.scale, rp.scale, 1);
      rp.mat.opacity = Math.max(0, rp.opacity);
      if (rp.opacity <= 0) {
        this.rippleGroup.remove(rp.mesh);
        rp.mesh.geometry.dispose();
        rp.mat.dispose();
        this.ripples.splice(i, 1);
      }
    }

    // Subtle camera shake during first 1s of crash (max 0.5 deg = 0.0087 rad)
    this.camera.position.copy(this.baseCameraPos);
    this.camera.lookAt(this.baseLookAt);
    if (nowMs < this.crashShakeUntilMs) {
      const maxRad = (0.45 * Math.PI) / 180.0; // <= 0.5 deg
      this.camera.rotation.z += (Math.random() - 0.5) * 2.0 * maxRad;
      this.camera.rotation.x += (Math.random() - 0.5) * 1.5 * maxRad;
    }

    this.renderer.render(this.scene, this.camera);
  }

  _render2D() {
    const canvas = this.canvas2d;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    ctx.fillStyle = "#070b12";
    ctx.fillRect(0, 0, w, h);

    // Top 55%: Mid price history chart
    const chartH = Math.floor(h * 0.54);
    const mids = this.midHistory2D;
    const minM = Math.min(...mids) - 10;
    const maxM = Math.max(...mids) + 10;
    const span = Math.max(1, maxM - minM);

    ctx.strokeStyle = "#1f2e47";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, chartH);
    ctx.lineTo(w, chartH);
    ctx.stroke();

    ctx.strokeStyle = this.latestTick.crash_active ? "#ef4444" : "#22d3ee";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    for (let i = 0; i < mids.length; i++) {
      const x = (i / (mids.length - 1)) * (w - 24) + 12;
      const y = chartH - 16 - ((mids[i] - minM) / span) * (chartH - 32);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Bottom 45%: Current Bid/Ask Depth Profile
    const depthTop = chartH + 16;
    const depthH = h - depthTop - 16;
    const row0 = this.history[0];
    if (row0) {
      const barW = Math.max(2, (w - 40) / COLS);
      for (let c = 0; c < COLS; c++) {
        const offset = c - HALF_COLS;
        const barH = Math.min(depthH, (row0.heights[c] / 20.0) * depthH);
        const x = 20 + c * barW;
        const y = h - 14 - barH;
        ctx.fillStyle = row0.crash
          ? "#ef4444"
          : offset < 0
          ? "#38bdf8"
          : offset > 0
          ? "#f59e0b"
          : "#22d3ee";
        ctx.fillRect(x, y, Math.max(1, barW - 1), barH);
      }
    }
  }
}
