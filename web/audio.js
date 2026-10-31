// WebAudio fill sonar pings + spoken risk briefing playback with Web Speech fallback (Section 9 & 10.4)

class AudioEngine {
  constructor() {
    this.muted = true; // Muted by default per SPEC Section 10.4
    this.ctx = null;
    this.briefingAudio = null;
    this.lastPingMs = 0;
  }

  _ensureContext() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        this.ctx = new AudioCtx();
      }
    }
    if (this.ctx && this.ctx.state === "suspended") {
      this.ctx.resume().catch(() => {});
    }
    return this.ctx;
  }

  toggleMute() {
    this.muted = !this.muted;
    if (!this.muted) {
      this._ensureContext();
      this.playFillPing("buy", 2.0);
    }
    return this.muted;
  }

  /**
   * Play a subtle sonar ping on fill; base pitch rises with realized volatility.
   * @param {"buy"|"sell"} side
   * @param {number} volatility
   */
  playFillPing(side = "buy", volatility = 1.8) {
    if (this.muted) return;
    const nowMs = performance.now();
    if (nowMs - this.lastPingMs < 55) return; // rate-limit rapid fills
    this.lastPingMs = nowMs;

    const ctx = this._ensureContext();
    if (!ctx) return;

    try {
      const t0 = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      // Base pitch rises with realized volatility; buy is slightly higher harmonic than sell
      const volClamped = Math.max(0.5, Math.min(18.0, Number(volatility) || 1.8));
      const baseFreq = (side === "buy" ? 520 : 440) + volClamped * 36;

      osc.type = "sine";
      osc.frequency.setValueAtTime(baseFreq, t0);
      osc.frequency.exponentialRampToValueAtTime(baseFreq * 1.18, t0 + 0.09);

      gain.gain.setValueAtTime(0.001, t0);
      gain.gain.linearRampToValueAtTime(0.08, t0 + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.16);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(t0);
      osc.stop(t0 + 0.17);
    } catch (_) {
      // Ignore WebAudio autoplay restrictions
    }
  }

  /**
   * Play the spoken risk briefing from `/api/briefing.wav` with Web Speech API fallback.
   * @param {string} briefingText
   */
  async playBriefing(briefingText = "") {
    try {
      if (this.briefingAudio) {
        this.briefingAudio.pause();
        this.briefingAudio = null;
      }
      const url = `/api/briefing.wav?t=${Date.now()}`;
      const audio = new Audio(url);
      this.briefingAudio = audio;
      await audio.play();
      return;
    } catch (_) {
      // Fallback to browser speechSynthesis
    }

    if ("speechSynthesis" in window && briefingText) {
      try {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(briefingText);
        utter.rate = 1.04;
        utter.pitch = 1.0;
        window.speechSynthesis.speak(utter);
      } catch (_) {
        // Ignore synthesis error in headless browsers
      }
    }
  }
}

export const audioEngine = new AudioEngine();
