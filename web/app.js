// Main Frontend Controller (ES Module) for TowerTwin (Section 10.4)

import { audioEngine } from "./audio.js?v=2";
import { PanelsController } from "./panels.js?v=2";
import { TwinVisualizer } from "./twin3d.js?v=2";

const panels = new PanelsController();
const twin = new TwinVisualizer(
  document.getElementById("twin-3d-canvas"),
  document.getElementById("twin-2d-canvas"),
  document.getElementById("twin-viewport")
);

let selectedMode = "repair";
let hasVoiceSample = false;
let pendingSignoff = null; // Stores the latest signoff_ready event payload
let activeSliderLimit = 100;
let liveMaxDrawdownCents = 0;

// DOM references
const mockBadgeEl = document.getElementById("mock-badge");
const stageLabelEl = document.getElementById("stage-label");
const botStatusPillEl = document.getElementById("bot-status-pill");
const botStatusTextEl = document.getElementById("bot-status-text");
const activeArtifactIdEl = document.getElementById("active-artifact-id");
const loopSummaryStatusEl = document.getElementById("loop-summary-status");

const sliderEl = document.getElementById("limit-slider");
const limitValueEl = document.getElementById("limit-value");
const modeRepairBtn = document.getElementById("mode-repair");
const modeScratchBtn = document.getElementById("mode-scratch");

const btnRun = document.getElementById("btn-run");
const btnOpenApprove = document.getElementById("btn-open-approve");
const btnCrash = document.getElementById("btn-crash");
const btnKill = document.getElementById("btn-kill");
const btnReset = document.getElementById("btn-reset");
const btnAudioMute = document.getElementById("btn-audio-mute");
const btnViewMode = document.getElementById("btn-view-mode");

const btnModalApprove = document.getElementById("btn-modal-approve");
const btnModalReject = document.getElementById("btn-modal-reject");
const btnModalClose = document.getElementById("btn-modal-close");
const btnPlayBriefing = document.getElementById("btn-play-briefing");

// Telemetry DOM
const metricMidEl = document.getElementById("metric-mid");
const metricPosEl = document.getElementById("metric-pos");
const metricPosBoxEl = document.getElementById("metric-pos-box");
const metricPnlEl = document.getElementById("metric-pnl");
const metricDdEl = document.getElementById("metric-dd");
const metricVolEl = document.getElementById("metric-vol");

// Overlay Banners
const haltedBannerEl = document.getElementById("twin-halted-banner");
const crashBannerEl = document.getElementById("twin-crash-banner");
const killedBannerEl = document.getElementById("twin-killed-banner");

async function initConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) return;
    const cfg = await res.json();

    if (cfg.mock_llm && mockBadgeEl) {
      mockBadgeEl.classList.remove("hidden");
    }
    hasVoiceSample = Boolean(cfg.has_voice_sample);

    if (cfg.desk_limits && cfg.desk_limits.position_limit) {
      activeSliderLimit = Number(cfg.desk_limits.position_limit);
      if (sliderEl) sliderEl.value = String(activeSliderLimit);
      if (limitValueEl) limitValueEl.textContent = `${activeSliderLimit} lots`;
      if (loopSummaryStatusEl) {
        loopSummaryStatusEl.innerHTML = `Desk rule: max inventory <strong>${activeSliderLimit} lots</strong>`;
      }
      panels.syncLimits(cfg.desk_limits);
    }
    if (cfg.artifact_id && activeArtifactIdEl) {
      activeArtifactIdEl.textContent = cfg.artifact_id;
    }
    if (cfg.bot_status && botStatusTextEl) {
      botStatusTextEl.textContent = cfg.bot_status;
    }
  } catch (_) {
    // Ignore transient startup fetch errors
  }
}

function connectSSE() {
  const es = new EventSource("/api/events");

  es.onmessage = (msg) => {
    try {
      const evt = JSON.parse(msg.data);
      handleEvent(evt);
    } catch (_) {
      // Ignore malformed SSE lines
    }
  };

  es.onerror = () => {
    // Browser EventSource automatically reconnects
  };
}

function handleEvent(evt) {
  if (!evt || !evt.type) return;

  switch (evt.type) {
    case "mock_mode": {
      if (evt.mock_llm && mockBadgeEl) {
        mockBadgeEl.classList.remove("hidden");
      }
      break;
    }

    case "loop_stage": {
      const stageText = evt.detail || evt.stage || "Running Verification Loop";
      if (stageLabelEl) stageLabelEl.textContent = stageText;
      panels.updateLoopRing(evt.stage, evt.iteration);
      break;
    }

    case "iteration_proposed": {
      const iter = Number(evt.iteration ?? 0);
      if (stageLabelEl) {
        stageLabelEl.textContent =
          iter === 0
            ? "Iteration 0 · Re-checking Current Desk Strategy"
            : `Iteration ${iter} · Running Sandbox Backtests`;
      }
      panels.onIterationProposed(evt);
      panels.updateLoopRing("sandbox", iter);
      break;
    }

    case "checks": {
      const iter = Number(evt.iteration ?? 0);
      panels.updateChecks(evt.checks);
      panels.onIterationChecks(evt);
      panels.updateLoopRing(evt.all_passed ? "checks" : "refine", iter);
      if (stageLabelEl) {
        const passedCount = (evt.checks || []).filter((c) => c.passed).length;
        stageLabelEl.textContent = `Iteration ${iter} · ${passedCount}/7 Checks Passed`;
      }
      break;
    }

    case "loop_passed": {
      if (stageLabelEl) {
        stageLabelEl.textContent = `In-Sample Loop Passed (Iter ${evt.iteration}) · Running 5×5 Sweep`;
      }
      panels.updateLoopRing("sandbox", evt.iteration);
      break;
    }

    case "loop_failed": {
      if (stageLabelEl) {
        stageLabelEl.textContent = `Verification Loop Failed (${evt.reason || "checks failed"})`;
      }
      if (btnRun) btnRun.disabled = false;
      break;
    }

    case "redteam_scenario": {
      panels.addRedteamScenario(evt);
      break;
    }

    case "sweep_cell": {
      panels.onSweepCell(evt);
      break;
    }

    case "sweep_done": {
      panels.onSweepDone(evt);
      if (stageLabelEl) {
        stageLabelEl.textContent = "5×5 Sweep Complete · Running Untouched Holdout Verification";
      }
      break;
    }

    case "final_verification": {
      if (Array.isArray(evt.checks)) {
        panels.updateChecks(evt.checks);
      }
      panels.updateLoopRing("checks");
      break;
    }

    case "signoff_ready": {
      pendingSignoff = evt;
      if (btnRun) btnRun.disabled = false;
      if (btnOpenApprove) btnOpenApprove.disabled = false;
      if (stageLabelEl) {
        stageLabelEl.textContent = `Sign-Off Ready · Artifact ${evt.artifact_id || ""}`;
      }
      panels.updateLoopRing("signoff");
      panels.showSignoffModal(evt, hasVoiceSample, true);
      // Auto-play spoken briefing once per SPEC Section 10.4
      audioEngine.playBriefing(evt.briefing_text || "");
      break;
    }

    case "signoff_blocked": {
      pendingSignoff = null;
      if (btnRun) btnRun.disabled = false;
      if (btnOpenApprove) btnOpenApprove.disabled = true;
      if (stageLabelEl) {
        stageLabelEl.textContent = "Sign-Off Blocked · Holdout Verification Failed";
      }
      panels.showSignoffModal(evt, hasVoiceSample, false);
      break;
    }

    case "approved": {
      pendingSignoff = null;
      liveMaxDrawdownCents = 0;
      if (metricDdEl) metricDdEl.textContent = "$0.00 / $0.00";
      panels.hideSignoffModal();
      if (btnOpenApprove) btnOpenApprove.disabled = true;
      if (btnRun) btnRun.disabled = false;
      if (activeArtifactIdEl && evt.artifact_id) {
        activeArtifactIdEl.textContent = evt.artifact_id;
      }
      if (stageLabelEl) {
        stageLabelEl.textContent = `Approved & Live · Artifact ${evt.artifact_id || ""}`;
      }
      panels.updateLoopRing("approved");
      break;
    }

    case "tick": {
      onTickEvent(evt);
      break;
    }

    case "crash_started": {
      if (crashBannerEl) crashBannerEl.classList.remove("hidden");
      if (stageLabelEl) stageLabelEl.textContent = "LIVE FLASH CRASH INJECTED";
      break;
    }

    case "halted": {
      if (haltedBannerEl) haltedBannerEl.classList.remove("hidden");
      break;
    }

    case "resumed": {
      if (haltedBannerEl) haltedBannerEl.classList.add("hidden");
      break;
    }

    case "killed": {
      if (killedBannerEl) killedBannerEl.classList.remove("hidden");
      if (botStatusTextEl) botStatusTextEl.textContent = "KILLED BY HUMAN";
      if (botStatusPillEl) botStatusPillEl.classList.add("killed");
      if (stageLabelEl) stageLabelEl.textContent = "KILLED BY HUMAN OVERRIDE";
      break;
    }

    case "model_error": {
      if (stageLabelEl) {
        stageLabelEl.textContent = `Model Warning: ${evt.message || "Retrying"}`;
      }
      break;
    }
  }
}

function onTickEvent(evt) {
  const midDollars = (Number(evt.mid || 10000) / 100.0).toFixed(2);
  const pos = Number(evt.position || 0);
  const engineLimit = (evt.limits && Number(evt.limits.position_limit)) || activeSliderLimit;
  const effectiveLimit = Math.min(engineLimit, activeSliderLimit);
  const breached = Boolean(evt.position_breach) || Math.abs(pos) > effectiveLimit;

  if (metricMidEl) metricMidEl.textContent = `$${midDollars}`;
  if (metricPosEl) metricPosEl.textContent = `${pos} / ${effectiveLimit}`;
  if (metricPosBoxEl) {
    metricPosBoxEl.classList.toggle("breach", breached);
  }

  const pnlDollars = Number(evt.pnl || 0) / 100.0;
  if (metricPnlEl) {
    const sign = pnlDollars >= 0 ? "+" : "-";
    metricPnlEl.textContent = `${sign}$${Math.abs(pnlDollars).toFixed(2)}`;
    metricPnlEl.className = `metric-value ${pnlDollars >= 0 ? "positive" : "negative"}`;
  }

  const currDdCents = Math.max(0, Number(evt.drawdown || 0));
  const serverMaxDdCents = Math.max(0, Number(evt.max_drawdown || 0));
  liveMaxDrawdownCents = Math.max(liveMaxDrawdownCents, currDdCents, serverMaxDdCents);
  const ddDollars = (currDdCents / 100.0).toFixed(2);
  const maxDdDollars = (liveMaxDrawdownCents / 100.0).toFixed(2);
  if (metricDdEl) metricDdEl.textContent = `$${ddDollars} / $${maxDdDollars}`;
  if (metricVolEl) metricVolEl.textContent = Number(evt.volatility || 0).toFixed(2);

  // Bot status pill in topbar
  if (evt.bot_status && botStatusTextEl) {
    botStatusTextEl.textContent = evt.bot_status;
  }
  if (botStatusPillEl) {
    botStatusPillEl.classList.toggle("breach", breached && !evt.killed);
    botStatusPillEl.classList.toggle("killed", Boolean(evt.killed));
  }
  if (evt.artifact_id && activeArtifactIdEl) {
    activeArtifactIdEl.textContent = evt.artifact_id;
  }

  // Overlays
  if (haltedBannerEl) {
    haltedBannerEl.classList.toggle("hidden", !evt.halted || Boolean(evt.killed));
  }
  if (crashBannerEl) {
    crashBannerEl.classList.toggle("hidden", !evt.crash_active);
  }
  if (killedBannerEl) {
    killedBannerEl.classList.toggle("hidden", !evt.killed);
  }

  // Play sonar ping on fills
  if (Array.isArray(evt.fills) && evt.fills.length > 0) {
    const lastFill = evt.fills[evt.fills.length - 1];
    audioEngine.playFillPing(lastFill.side, evt.volatility);
  }

  twin.onTick(evt);
  panels.updateSparklines(evt.pnl, pos, effectiveLimit);
}

// ==================== CONTROLS & ACTIONS ====================
if (sliderEl) {
  const onSliderInput = () => {
    activeSliderLimit = Number(sliderEl.value);
    if (limitValueEl) limitValueEl.textContent = `${activeSliderLimit} lots`;
    if (loopSummaryStatusEl) {
      loopSummaryStatusEl.innerHTML = `Desk rule: max inventory <strong>${activeSliderLimit} lots</strong>`;
    }
  };
  sliderEl.addEventListener("input", onSliderInput);
  sliderEl.addEventListener("change", onSliderInput);
}

if (modeRepairBtn && modeScratchBtn) {
  modeRepairBtn.addEventListener("click", () => {
    selectedMode = "repair";
    modeRepairBtn.classList.add("active");
    modeScratchBtn.classList.remove("active");
    if (btnRun) btnRun.textContent = "Run Repair";
  });
  modeScratchBtn.addEventListener("click", () => {
    selectedMode = "scratch";
    modeScratchBtn.classList.add("active");
    modeRepairBtn.classList.remove("active");
    if (btnRun) btnRun.textContent = "Run From Scratch";
  });
}

if (btnRun) {
  btnRun.addEventListener("click", async () => {
    const limit = Number(sliderEl ? sliderEl.value : 60);
    activeSliderLimit = limit;
    btnRun.disabled = true;
    if (btnOpenApprove) btnOpenApprove.disabled = true;
    pendingSignoff = null;
    panels.hideSignoffModal();
    panels.setChecksPending();
    panels.resetTimeline();
    panels.resetRedteam();
    panels.clearHeatmap();

    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_limit: limit, mode: selectedMode }),
      });
      if (!res.ok) {
        btnRun.disabled = false;
      }
    } catch (_) {
      btnRun.disabled = false;
    }
  });
}

async function submitApproval() {
  if (!pendingSignoff || !pendingSignoff.artifact_hash) return;
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artifact_hash: pendingSignoff.artifact_hash }),
    });
    if (res.ok) {
      panels.hideSignoffModal();
      if (btnOpenApprove) btnOpenApprove.disabled = true;
    }
  } catch (_) {
    // Ignore transient network error
  }
}

if (btnModalApprove) {
  btnModalApprove.addEventListener("click", submitApproval);
}

if (btnOpenApprove) {
  btnOpenApprove.addEventListener("click", () => {
    const modal = document.getElementById("signoff-modal");
    if (modal && modal.classList.contains("hidden") && pendingSignoff) {
      panels.showSignoffModal(pendingSignoff, hasVoiceSample, true);
    } else {
      submitApproval();
    }
  });
}

if (btnModalReject) {
  btnModalReject.addEventListener("click", () => panels.hideSignoffModal());
}
if (btnModalClose) {
  btnModalClose.addEventListener("click", () => panels.hideSignoffModal());
}

if (btnPlayBriefing) {
  btnPlayBriefing.addEventListener("click", () => {
    const text = (pendingSignoff && pendingSignoff.briefing_text) || "";
    audioEngine.playBriefing(text);
  });
}

if (btnCrash) {
  btnCrash.addEventListener("click", async () => {
    await fetch("/api/crash", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  });
}

if (btnKill) {
  btnKill.addEventListener("click", async () => {
    await fetch("/api/kill", { method: "POST" });
  });
}

if (btnReset) {
  btnReset.addEventListener("click", async () => {
    const res = await fetch("/api/reset", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      liveMaxDrawdownCents = 0;
      if (metricDdEl) metricDdEl.textContent = "$0.00 / $0.00";
      activeSliderLimit = (data.limits && Number(data.limits.position_limit)) || 100;
      if (sliderEl) sliderEl.value = String(activeSliderLimit);
      if (limitValueEl) limitValueEl.textContent = `${activeSliderLimit} lots`;
      if (loopSummaryStatusEl) {
        loopSummaryStatusEl.innerHTML = `Desk rule: max inventory <strong>${activeSliderLimit} lots</strong>`;
      }
      if (killedBannerEl) killedBannerEl.classList.add("hidden");
      if (haltedBannerEl) haltedBannerEl.classList.add("hidden");
      if (crashBannerEl) crashBannerEl.classList.add("hidden");
      if (stageLabelEl) stageLabelEl.textContent = "Live Digital Twin · Reset to Desk Strategy";
      panels.syncLimits(data.limits);
      panels.resetTimeline();
      panels.resetRedteam();
      panels.clearHeatmap();
      panels.updateLoopRing("idle");
      if (btnRun) btnRun.disabled = false;
      if (btnOpenApprove) btnOpenApprove.disabled = true;
    }
  });
}

if (btnAudioMute) {
  btnAudioMute.addEventListener("click", () => {
    const isMuted = audioEngine.toggleMute();
    btnAudioMute.textContent = isMuted ? "🔇 Sonar Off" : "🔊 Sonar On";
  });
}

if (btnViewMode) {
  btnViewMode.addEventListener("click", () => {
    const newMode = twin.toggleMode();
    btnViewMode.textContent = newMode === "3d" ? "2D Mode" : "3D Mode";
  });
}

// Initialize on load
initConfig();
connectSSE();
