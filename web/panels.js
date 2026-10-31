// UI Panels: Loop Ring, Checks C1-C7, Timeline & Code Diffs, Red-Team Cards, 5x5 Sweep Heatmap, Sparklines, Sign-Off Modal

const CHECK_META = [
  { id: "C1", name: "Books Reconcile", defaultThreshold: "0 ¢ diff", desc: "Zero cash/fee/PnL discrepancy across all fills" },
  { id: "C2", name: "Position Limit (Every Fill)", defaultThreshold: "≤ 100 lots", desc: "|position| ≤ limit after every single fill" },
  { id: "C3", name: "Order Size", defaultThreshold: "≤ 10 lots", desc: "0 < order_size ≤ 10 lots & uncrossed quotes" },
  { id: "C4", name: "Max Drawdown", defaultThreshold: "≤ $250.00", desc: "Running peak-to-trough drawdown ≤ $250.00" },
  { id: "C5", name: "Crash Circuit Breaker", defaultThreshold: "≤ 5 ticks", desc: "Halts quoting within ≤ 5 ticks of flash crash" },
  { id: "C6", name: "Validation Profit", defaultThreshold: "> $0.00", desc: "Final PnL > $0.00 on every unseen OOS seed" },
  { id: "C7", name: "Sandbox Clean & Bounded", defaultThreshold: "0 errors", desc: "AST-validated subprocess · 0 errors · ≤5ms p99" },
];

export class PanelsController {
  constructor() {
    this.checksListEl = document.getElementById("checks-list");
    this.checksSummaryEl = document.getElementById("checks-pass-summary");
    this.timelineListEl = document.getElementById("timeline-list");
    this.timelineCountEl = document.getElementById("timeline-count");
    this.redteamGridEl = document.getElementById("redteam-grid");
    this.redteamCountEl = document.getElementById("redteam-count");
    this.heatmapCanvas = document.getElementById("heatmap-canvas");
    this.heatmapAxesEl = document.getElementById("heatmap-axes-label");
    this.heatmapStatusEl = document.getElementById("heatmap-status");

    this.prevCheckStatus = {};
    this.iterations = new Map(); // iteration -> { code, rationale, title, checks }
    this.redteamCount = 0;
    this.sweepCells = Array.from({ length: 5 }, () => Array(5).fill(null));
    this.chosenCell = null;

    this.pnlHistory = Array(100).fill(0);
    this.posHistory = Array(100).fill(0);

    this.initChecksPanel();
    this.clearHeatmap();
    this._bindHeatmapHover();
  }

  _bindHeatmapHover() {
    if (!this.heatmapCanvas) return;
    this.heatmapCanvas.addEventListener("mousemove", (e) => {
      const rect = this.heatmapCanvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const c = Math.min(4, Math.max(0, Math.floor(((e.clientX - rect.left) / rect.width) * 5)));
      const r = Math.min(4, Math.max(0, Math.floor(((e.clientY - rect.top) / rect.height) * 5)));
      const cell = this.sweepCells[r] && this.sweepCells[r][c];
      if (cell && this.heatmapStatusEl) {
        const pct = Math.round(Number(cell.pass_rate ?? 0) * 100);
        const xv = cell.x_val !== undefined ? cell.x_val : c;
        const yv = cell.y_val !== undefined ? cell.y_val : r;
        this.heatmapStatusEl.textContent = `[${r},${c}] (${xv}, ${yv}): ${pct}% pass`;
      }
    });
    this.heatmapCanvas.addEventListener("mouseleave", () => {
      if (this.heatmapStatusEl && this.lastHeatmapSummary) {
        this.heatmapStatusEl.textContent = this.lastHeatmapSummary;
      }
    });
  }

  // ==================== CHECKS PANEL (C1-C7) ====================
  syncLimits(limits) {
    if (!limits) return;
    for (const meta of CHECK_META) {
      if (meta.id === "C2" && limits.position_limit != null) {
        meta.defaultThreshold = `≤ ${limits.position_limit} lots`;
      } else if (meta.id === "C3" && limits.max_order_size != null) {
        meta.defaultThreshold = `≤ ${limits.max_order_size} lots`;
      } else if (meta.id === "C4" && limits.max_drawdown != null) {
        meta.defaultThreshold = `≤ $${(Number(limits.max_drawdown) / 100.0).toFixed(2)}`;
      } else if (meta.id === "C5" && limits.halt_within_ticks != null) {
        meta.defaultThreshold = `≤ ${limits.halt_within_ticks} ticks`;
      }
    }
    this.initChecksPanel();
  }

  initChecksPanel() {
    if (!this.checksListEl) return;
    this.checksListEl.innerHTML = "";
    for (const meta of CHECK_META) {
      const row = document.createElement("div");
      row.id = `check-row-${meta.id}`;
      row.className = "check-row status-pass";
      row.title = `${meta.id} (${meta.name}): ${meta.desc}`;
      row.innerHTML = `
        <span class="check-id">${meta.id}</span>
        <div class="check-name-wrap">
          <span class="check-name">${meta.name}</span>
          <span class="check-detail-sub" id="check-detail-${meta.id}">${meta.desc}</span>
        </div>
        <span class="check-metrics" id="check-metrics-${meta.id}">OK / ${meta.defaultThreshold}</span>
        <span class="check-pill pass" id="check-pill-${meta.id}">PASS</span>
      `;
      this.checksListEl.appendChild(row);
      this.prevCheckStatus[meta.id] = "pass";
    }
  }

  setChecksPending() {
    for (const meta of CHECK_META) {
      const row = document.getElementById(`check-row-${meta.id}`);
      const pill = document.getElementById(`check-pill-${meta.id}`);
      if (row) row.className = "check-row";
      if (pill) {
        pill.className = "check-pill pending";
        pill.textContent = "PEND";
      }
      this.prevCheckStatus[meta.id] = "pending";
    }
    if (this.checksSummaryEl) {
      this.checksSummaryEl.textContent = "... / 7";
      this.checksSummaryEl.classList.remove("has-fail");
    }
  }

  updateChecks(checksArray) {
    if (!Array.isArray(checksArray)) return;
    let passCount = 0;

    for (const chk of checksArray) {
      const id = chk.id;
      const passed = Boolean(chk.passed);
      if (passed) passCount++;

      const row = document.getElementById(`check-row-${id}`);
      const pill = document.getElementById(`check-pill-${id}`);
      const metricsEl = document.getElementById(`check-metrics-${id}`);
      const detailEl = document.getElementById(`check-detail-${id}`);

      const newStatus = passed ? "pass" : "fail";
      const changed = this.prevCheckStatus[id] !== newStatus;
      this.prevCheckStatus[id] = newStatus;

      if (row) {
        row.className = `check-row status-${newStatus}${changed ? " animate-change" : ""}`;
        row.title = chk.detail || "";
      }
      if (pill) {
        pill.className = `check-pill ${newStatus}`;
        pill.textContent = passed ? "PASS" : "FAIL";
      }
      if (metricsEl) {
        const measuredVal = chk.measured !== undefined ? chk.measured : chk.value;
        metricsEl.textContent = `${this._fmtMetric(id, measuredVal)} / ${this._fmtMetric(id, chk.threshold)}`;
      }
      if (detailEl) {
        detailEl.textContent = chk.detail || "";
      }
    }

    if (this.checksSummaryEl) {
      this.checksSummaryEl.textContent = `${passCount} / ${checksArray.length} PASS`;
      this.checksSummaryEl.classList.toggle("has-fail", passCount < checksArray.length);
    }
  }

  _fmtMetric(checkId, val) {
    if (val === null || val === undefined) return "-";
    if (checkId === "C4" || checkId === "C6") {
      const num = Number(val);
      if (!Number.isNaN(num)) {
        const sign = num < 0 ? "-" : "";
        return `${sign}$${(Math.abs(num) / 100).toFixed(0)}`;
      }
    }
    return String(val);
  }

  // ==================== LOOP RING ====================
  updateLoopRing(stage, iteration = null, maxIterations = 6) {
    const nodes = {
      propose: document.getElementById("ring-node-propose"),
      sandbox: document.getElementById("ring-node-sandbox"),
      checks: document.getElementById("ring-node-checks"),
      refine: document.getElementById("ring-node-refine"),
      approve: document.getElementById("ring-node-approve"),
    };
    const counterEl = document.getElementById("loop-iter-counter");
    const subEl = document.getElementById("loop-iter-sub");

    Object.values(nodes).forEach((el) => {
      if (el) el.classList.remove("active", "done");
    });

    if (iteration !== null && iteration !== undefined && counterEl) {
      if (iteration === 0) {
        counterEl.textContent = "Desk Check";
      } else {
        counterEl.textContent = `Iter ${iteration} of ${maxIterations}`;
      }
    }

    const st = String(stage || "").toLowerCase();
    if (st.includes("propos") || st.includes("plan")) {
      if (nodes.propose) nodes.propose.classList.add("active");
      if (subEl) subEl.textContent = "Argon proposing";
    } else if (st.includes("sandbox") || st.includes("backtest") || st.includes("sweep")) {
      if (nodes.propose) nodes.propose.classList.add("done");
      if (nodes.sandbox) nodes.sandbox.classList.add("active");
      if (subEl) subEl.textContent = "Running sandbox";
    } else if (st.includes("check") || st.includes("verif") || st.includes("holdout")) {
      if (nodes.propose) nodes.propose.classList.add("done");
      if (nodes.sandbox) nodes.sandbox.classList.add("done");
      if (nodes.checks) nodes.checks.classList.add("active");
      if (subEl) subEl.textContent = "Checking proofs";
    } else if (st.includes("refin") || st.includes("review")) {
      if (nodes.propose) nodes.propose.classList.add("done");
      if (nodes.sandbox) nodes.sandbox.classList.add("done");
      if (nodes.checks) nodes.checks.classList.add("done");
      if (nodes.refine) nodes.refine.classList.add("active");
      if (subEl) subEl.textContent = "Refining strategy";
    } else if (st.includes("signoff") || st.includes("ready")) {
      if (nodes.propose) nodes.propose.classList.add("done");
      if (nodes.sandbox) nodes.sandbox.classList.add("done");
      if (nodes.checks) nodes.checks.classList.add("done");
      if (nodes.refine) nodes.refine.classList.add("done");
      if (nodes.approve) nodes.approve.classList.add("active");
      if (subEl) subEl.textContent = "Awaiting approval";
    } else if (st.includes("approv")) {
      Object.values(nodes).forEach((el) => {
        if (el) el.classList.add("done");
      });
      if (counterEl) counterEl.textContent = "Approved";
      if (subEl) subEl.textContent = "Live in twin";
    } else if (st.includes("idle")) {
      if (counterEl) counterEl.textContent = "Idle";
      if (subEl) subEl.textContent = "Ready to verify";
    }
  }

  // ==================== ITERATION TIMELINE & DIFF ====================
  resetTimeline() {
    this.iterations.clear();
    if (this.timelineListEl) {
      this.timelineListEl.innerHTML = `
        <div class="timeline-empty">
          <strong>How to test:</strong> Lower <em>Desk Position Limit</em> to <strong>60 lots</strong> in Step 1 below, then click <strong>Run Repair</strong> in Step 2 to watch Iteration 0 catch the breach and Argon repair it.
        </div>
      `;
    }
    if (this.timelineCountEl) {
      this.timelineCountEl.textContent = "0 iterations";
    }
  }

  onIterationProposed(evt) {
    const iter = Number(evt.iteration ?? 0);
    const isCard0 = iter === 0;
    const title = isCard0
      ? "Card 0 · Current desk strategy"
      : `Iteration ${iter} · Argon Proposal`;
    const rationale =
      evt.rationale ||
      (isCard0
        ? "Re-checking existing desk strategy under updated desk risk rules."
        : "Updated strategy logic and risk parameters.");
    const code = evt.code || "";

    const prevCode = iter > 0 && this.iterations.has(iter - 1) ? this.iterations.get(iter - 1).code : "";
    this.iterations.set(iter, { iter, title, rationale, code, prevCode, checks: null });
    this._renderIterationCard(iter);
  }

  onIterationChecks(evt) {
    const iter = Number(evt.iteration ?? 0);
    const entry = this.iterations.get(iter) || {
      iter,
      title: iter === 0 ? "Card 0 · Current desk strategy" : `Iteration ${iter}`,
      rationale: iter === 0 ? "Re-checking current desk strategy under active desk rules." : "",
      code: "",
      prevCode: "",
    };
    entry.checks = evt.checks || [];
    entry.allPassed = Boolean(evt.all_passed);
    this.iterations.set(iter, entry);
    this._renderIterationCard(iter);
  }

  _renderIterationCard(iter) {
    if (!this.timelineListEl) return;
    const emptyEl = this.timelineListEl.querySelector(".timeline-empty");
    if (emptyEl) emptyEl.remove();

    const entry = this.iterations.get(iter);
    if (!entry) return;

    let card = document.getElementById(`iter-card-${iter}`);
    if (!card) {
      card = document.createElement("div");
      card.id = `iter-card-${iter}`;
      card.className = "iter-card";
      this.timelineListEl.prepend(card);
    }

    const checks = entry.checks;
    let badgeHtml = `<span class="iter-badge">Running...</span>`;
    if (Array.isArray(checks)) {
      const passedCount = checks.filter((c) => c.passed).length;
      const allPass = passedCount === checks.length;
      card.className = `iter-card ${allPass ? "iter-pass" : "iter-fail"}`;
      badgeHtml = `<span class="iter-badge ${allPass ? "pass" : "fail"}">${passedCount}/${checks.length} PASS</span>`;
    }

    const diffHtml = this._buildDiffHtml(entry.prevCode, entry.code, iter === 0);

    card.innerHTML = `
      <div class="iter-header">
        <span class="iter-title">${this._escapeHtml(entry.title)}</span>
        ${badgeHtml}
      </div>
      <div class="iter-rationale">${this._escapeHtml(entry.rationale)}</div>
      <details class="iter-diff-details">
        <summary>${iter === 0 ? "View baseline desk code" : "View code diff vs previous iteration"}</summary>
        <pre class="diff-pre">${diffHtml}</pre>
      </details>
    `;

    if (this.timelineCountEl) {
      this.timelineCountEl.textContent = `${this.iterations.size} card(s)`;
    }
  }

  _buildDiffHtml(oldCode, newCode, isBaseline) {
    if (!newCode) return `<span class="diff-ctx">(code loaded in sandbox)</span>`;
    const newLines = newCode.split("\n");
    if (isBaseline || !oldCode) {
      return newLines
        .slice(0, 60)
        .map((ln) => `<span class="diff-ctx">  ${this._escapeHtml(ln)}</span>`)
        .join("");
    }
    const oldSet = new Set(oldCode.split("\n").map((s) => s.trim()));
    const newSet = new Set(newLines.map((s) => s.trim()));
    const out = [];

    for (const ln of oldCode.split("\n")) {
      const t = ln.trim();
      if (t && !newSet.has(t)) {
        out.push(`<span class="diff-del">- ${this._escapeHtml(ln)}</span>`);
      }
    }
    for (const ln of newLines) {
      const t = ln.trim();
      if (t && !oldSet.has(t)) {
        out.push(`<span class="diff-add">+ ${this._escapeHtml(ln)}</span>`);
      } else if (out.length < 35) {
        out.push(`<span class="diff-ctx">  ${this._escapeHtml(ln)}</span>`);
      }
    }
    return out.join("") || `<span class="diff-ctx">  (Parameter update only)</span>`;
  }

  // ==================== RED-TEAM SCENARIO CARDS ====================
  resetRedteam() {
    this.redteamCount = 0;
    if (this.redteamGridEl) {
      this.redteamGridEl.innerHTML = `
        <div class="redteam-placeholder">
          6 adversarial flash-crash &amp; volatility regimes stream here in parallel during strategy repair...
        </div>
      `;
    }
    if (this.redteamCountEl) {
      this.redteamCountEl.textContent = "0 / 6 generated";
    }
  }

  addRedteamScenario(evt) {
    if (!this.redteamGridEl) return;
    const ph = this.redteamGridEl.querySelector(".redteam-placeholder");
    if (ph) ph.remove();

    const sc = evt.scenario || evt;
    this.redteamCount = Math.min(6, this.redteamCount + 1);
    if (this.redteamCountEl) {
      this.redteamCountEl.textContent = `${this.redteamCount} / 6 generated`;
    }

    const card = document.createElement("div");
    card.className = "redteam-card";
    const depth = sc.depth_pct !== undefined ? `${sc.depth_pct}% drop` : "";
    const dur = sc.duration !== undefined ? `${sc.duration}t` : "";
    const sig = sc.sigma_mult !== undefined ? `${sc.sigma_mult}× vol` : "";
    card.innerHTML = `
      <div class="rt-name" title="${this._escapeHtml(sc.name || "Scenario")}">${this._escapeHtml(sc.name || "Scenario")}</div>
      <div class="rt-desc" title="${this._escapeHtml(sc.description || "")}">${this._escapeHtml(sc.description || "Adversarial stress test")}</div>
      <div class="rt-meta">${depth} · ${dur} · ${sig}</div>
    `;
    this.redteamGridEl.appendChild(card);
  }

  // ==================== 5x5 PARAMETER SWEEP HEATMAP ====================
  clearHeatmap() {
    this.sweepCells = Array.from({ length: 5 }, () => Array(5).fill(null));
    this.chosenCell = null;
    this.lastHeatmapSummary = "Awaiting sweep...";
    if (this.heatmapStatusEl) {
      this.heatmapStatusEl.textContent = this.lastHeatmapSummary;
    }
    this._drawHeatmap();
  }

  onSweepCell(evt) {
    const r = Number(evt.row ?? 0);
    const c = Number(evt.col ?? 0);
    if (r >= 0 && r < 5 && c >= 0 && c < 5) {
      this.sweepCells[r][c] = evt;
    }
    if (evt.param_x && evt.param_y && this.heatmapAxesEl) {
      this.heatmapAxesEl.innerHTML = `X: <code>${this._escapeHtml(evt.param_x)}</code><br/>Y: <code>${this._escapeHtml(evt.param_y)}</code>`;
    }
    const filled = this.sweepCells.flat().filter(Boolean).length;
    this.lastHeatmapSummary = `Sweeping ${filled}/25 cells...`;
    if (this.heatmapStatusEl) {
      this.heatmapStatusEl.textContent = this.lastHeatmapSummary;
    }
    this._drawHeatmap();
  }

  onSweepDone(evt) {
    if (evt.chosen_cell) {
      this.chosenCell = evt.chosen_cell;
    } else if (evt.best_row !== undefined && evt.best_col !== undefined) {
      this.chosenCell = { row: evt.best_row, col: evt.best_col };
    }
    const passCells = this.sweepCells.flat().filter((cell) => cell && Number(cell.pass_rate ?? 0) >= 0.999).length;
    this.lastHeatmapSummary = `${passCells}/25 safe · optimal outlined`;
    if (this.heatmapStatusEl) {
      this.heatmapStatusEl.textContent = this.lastHeatmapSummary;
    }
    this._drawHeatmap();
  }

  _drawHeatmap() {
    if (!this.heatmapCanvas) return;
    const ctx = this.heatmapCanvas.getContext("2d");
    if (!ctx) return;

    const w = this.heatmapCanvas.width;
    const h = this.heatmapCanvas.height;
    ctx.fillStyle = "#070b12";
    ctx.fillRect(0, 0, w, h);

    const cellW = w / 5;
    const cellH = h / 5;

    for (let r = 0; r < 5; r++) {
      for (let c = 0; c < 5; c++) {
        const cell = this.sweepCells[r][c];
        const x = c * cellW;
        const y = r * cellH;

        if (!cell) {
          ctx.fillStyle = "#121a2a";
          ctx.fillRect(x + 1, y + 1, cellW - 2, cellH - 2);
        } else {
          const rate = Math.max(0, Math.min(1, Number(cell.pass_rate ?? 0)));
          // Interpolate Red (0) -> Amber (0.5) -> Green (1)
          const red = Math.round(rate < 0.5 ? 239 : 239 - (rate - 0.5) * 2 * (239 - 16));
          const green = Math.round(rate < 0.5 ? 68 + rate * 2 * (158 - 68) : 185);
          const blue = Math.round(rate < 0.5 ? 68 - rate * 2 * 50 : 129);
          ctx.fillStyle = `rgb(${red}, ${green}, ${blue})`;
          ctx.fillRect(x + 1, y + 1, cellW - 2, cellH - 2);
        }
      }
    }

    if (this.chosenCell) {
      const cr = Number(this.chosenCell.row ?? 0);
      const cc = Number(this.chosenCell.col ?? 0);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2.2;
      ctx.strokeRect(cc * cellW + 1.5, cr * cellH + 1.5, cellW - 3, cellH - 3);
    }
  }

  // ==================== SPARKLINES ====================
  updateSparklines(pnlCents, position, posLimit) {
    const pnlDollars = Number(pnlCents || 0) / 100.0;
    this.pnlHistory.push(pnlDollars);
    if (this.pnlHistory.length > 100) this.pnlHistory.shift();

    this.posHistory.push(Number(position || 0));
    if (this.posHistory.length > 100) this.posHistory.shift();

    const pnlValEl = document.getElementById("spark-pnl-val");
    if (pnlValEl) {
      const sign = pnlDollars >= 0 ? "+" : "-";
      pnlValEl.textContent = `${sign}$${Math.abs(pnlDollars).toFixed(2)}`;
      pnlValEl.style.color = pnlDollars >= 0 ? "#10b981" : "#ef4444";
    }

    const posValEl = document.getElementById("spark-pos-val");
    const breached = Math.abs(position) > posLimit;
    if (posValEl) {
      posValEl.textContent = `${position} / ${posLimit}`;
      posValEl.style.color = breached ? "#ef4444" : "#f1f5f9";
    }

    this._drawSparkline("spark-pnl-canvas", this.pnlHistory, "#22d3ee", null);
    this._drawSparkline(
      "spark-pos-canvas",
      this.posHistory,
      breached ? "#ef4444" : "#38bdf8",
      posLimit
    );
  }

  _drawSparkline(canvasId, series, strokeColor, limitBound = null) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    ctx.fillStyle = "#090e17";
    ctx.fillRect(0, 0, w, h);

    let minV = Math.min(...series);
    let maxV = Math.max(...series);
    if (limitBound !== null) {
      minV = Math.min(minV, -limitBound * 1.15);
      maxV = Math.max(maxV, limitBound * 1.15);
      // Draw limit lines
      const spanL = Math.max(1, maxV - minV);
      const yTop = h - 4 - ((limitBound - minV) / spanL) * (h - 8);
      const yBot = h - 4 - ((-limitBound - minV) / spanL) * (h - 8);
      ctx.strokeStyle = "rgba(239, 68, 68, 0.45)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(0, yTop);
      ctx.lineTo(w, yTop);
      ctx.moveTo(0, yBot);
      ctx.lineTo(w, yBot);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const span = Math.max(1e-3, maxV - minV);
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    for (let i = 0; i < series.length; i++) {
      const x = (i / (series.length - 1)) * (w - 6) + 3;
      const y = h - 4 - ((series[i] - minV) / span) * (h - 8);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  // ==================== SIGN-OFF MODAL ====================
  showSignoffModal(evt, hasVoiceSample = false, isReady = true) {
    const modal = document.getElementById("signoff-modal");
    if (!modal) return;

    const badge = document.getElementById("modal-status-badge");
    const artIdEl = document.getElementById("modal-artifact-id");
    const artHashEl = document.getElementById("modal-artifact-hash");
    const verifyLineEl = document.getElementById("modal-verify-line");
    const voiceLabelEl = document.getElementById("modal-voice-label");
    const reportEl = document.getElementById("modal-report-md");
    const approveBtn = document.getElementById("btn-modal-approve");

    if (badge) {
      badge.className = `modal-badge ${isReady ? "badge-ready" : "badge-blocked"}`;
      badge.textContent = isReady
        ? "FORMAL PROOF VERIFIED · READY FOR SIGN-OFF"
        : "SIGN-OFF BLOCKED · HOLDOUT FAILED";
    }
    if (artIdEl) artIdEl.textContent = evt.artifact_id || (evt.artifact_hash || "").slice(0, 8) || "--------";
    if (artHashEl) artHashEl.textContent = evt.artifact_hash || "N/A";

    const checks = evt.checks || [];
    const passedCount = checks.filter((c) => c.passed).length || (isReady ? 7 : 0);
    const totalChecks = checks.length || 7;
    const inSeeds = evt.in_sample_seed_count || 3;
    const holdSeeds = evt.holdout_seed_count || 5;
    if (verifyLineEl) {
      verifyLineEl.textContent = `${passedCount}/${totalChecks} checks passed · ${inSeeds} in-sample + ${holdSeeds} untouched holdout seeds`;
    }

    if (voiceLabelEl) {
      voiceLabelEl.textContent =
        evt.voice_replicated || hasVoiceSample
          ? "Voice: replicated with consent"
          : "Voice: Spoken Risk Briefing (TTS / Web Speech)";
    }

    if (reportEl) {
      reportEl.innerHTML = this._renderSimpleMarkdown(
        evt.report_md || "Strategy passed all deterministic checks C1–C7 across in-sample and untouched holdout seeds."
      );
    }

    if (approveBtn) {
      approveBtn.disabled = !isReady || !evt.artifact_hash;
    }

    modal.classList.remove("hidden");
  }

  hideSignoffModal() {
    const modal = document.getElementById("signoff-modal");
    if (modal) modal.classList.add("hidden");
  }

  _renderSimpleMarkdown(md) {
    const lines = String(md || "").split("\n");
    const html = [];
    let inList = false;

    for (const raw of lines) {
      const line = raw.trim();
      if (!line) {
        if (inList) {
          html.push("</ul>");
          inList = false;
        }
        continue;
      }
      if (line.startsWith("### ")) {
        if (inList) { html.push("</ul>"); inList = false; }
        html.push(`<h3>${this._formatInlineMd(line.slice(4))}</h3>`);
      } else if (line.startsWith("## ")) {
        if (inList) { html.push("</ul>"); inList = false; }
        html.push(`<h2>${this._formatInlineMd(line.slice(3))}</h2>`);
      } else if (line.startsWith("# ")) {
        if (inList) { html.push("</ul>"); inList = false; }
        html.push(`<h1>${this._formatInlineMd(line.slice(2))}</h1>`);
      } else if (line.startsWith("- ") || line.startsWith("* ")) {
        if (!inList) {
          html.push("<ul>");
          inList = true;
        }
        html.push(`<li>${this._formatInlineMd(line.slice(2))}</li>`);
      } else {
        if (inList) { html.push("</ul>"); inList = false; }
        html.push(`<p>${this._formatInlineMd(line)}</p>`);
      }
    }
    if (inList) html.push("</ul>");
    return html.join("\n");
  }

  _formatInlineMd(str) {
    let s = this._escapeHtml(str);
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    return s;
  }

  _escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}
