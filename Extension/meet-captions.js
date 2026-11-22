/**
 * meet-captions.js  –  Content script for Google Meet (https://meet.google.com/*)
 *
 * Direct multi-turn speaker isolation:
 * Maps each spoken caption block directly to its individual speaker turn,
 * preventing different speakers from merging together.
 */

(function () {
  "use strict";

  if (window.__mmcCaptionsObserver) {
    try { window.__mmcCaptionsObserver.disconnect(); } catch (_) {}
  }
  window.__mmcCaptionsInjected = true;

  let nextTurnId = 1;
  const recentTurns = new Map(); // turnId -> lastSentText
  let debounceTimer = null;
  let participantInterval = null;
  let watchdogInterval = null;
  let observer = null;

  function cleanup() {
    try {
      if (participantInterval) { clearInterval(participantInterval); participantInterval = null; }
      if (watchdogInterval) { clearInterval(watchdogInterval); watchdogInterval = null; }
      if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
      if (observer) { observer.disconnect(); observer = null; }
      window.__mmcCaptionsObserver = null;
    } catch (_) {}
  }

  function safeSendMessage(msg) {
    try {
      if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) {
        cleanup();
        return;
      }
      const res = chrome.runtime.sendMessage(msg);
      if (res && typeof res.catch === "function") {
        res.catch((err) => {
          if (err && String(err.message || "").includes("Extension context invalidated")) {
            cleanup();
          }
        });
      }
    } catch (_) {
      cleanup();
    }
  }

  function log(...args) {
    console.log("[Meeting Minutes]", ...args);
  }

  function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // ── Helper: find speaker in a turn row ────────────────────────────────────
  function findSpeakerInRow(row, speechText) {
    if (!row) return "";

    // 1. Google Meet speaker name classes
    const speakerEl = row.querySelector(
      ".zs7s8d, .NWpY1d, .KcIKyf, [data-self-name], .jT5e98, div[class*='speaker' i], span[class*='speaker' i]"
    );
    if (speakerEl && speakerEl.textContent.trim()) {
      return speakerEl.textContent.trim();
    }

    // 2. Avatar img alt (e.g. alt="Shivsharan Sanjawad" or alt="You")
    const img = row.querySelector("img[alt]");
    if (img && img.alt && img.alt.trim() && img.alt.length < 50) {
      return img.alt.trim();
    }

    // 3. Mathematical text difference: Turn Row Text minus Speech Text = Speaker Info!
    // Example: "S Shivsharan Sanjawad Spoken text..." -> "Shivsharan Sanjawad"
    if (speechText) {
      const rowText = row.textContent.trim();
      let candidate = rowText.replace(speechText, "").trim();
      // Remove single initial avatar letter (e.g. "S Shivsharan..." -> "Shivsharan...")
      candidate = candidate.replace(/^[A-Za-z]\s+/, "");
      candidate = candidate.replace(/[:\n\r\t]/g, " ").trim();
      if (candidate.length >= 2 && candidate.length <= 40) {
        return candidate;
      }
    }

    return "";
  }

  // ── Names: own name + participants (independent of recording) ─────────────
  const isYou = (n) => /^(you|me)$/i.test((n || "").trim());
  // Google Meet puts button labels ("Mute", "Pin X to your main screen", "Reframe", ...) inside the same tiles as names.
  const UI_TEXT = /\b(pin|unpin|mute|unmute|reframe|remove|spotlight|present|presenting|turn (on|off)|more options|raise hand|captions?|camera|microphone|main screen|your main screen|someone else|can't|cannot|you can|options|menu|settings|meeting|joined|left|waiting|admit|deny|everyone|annotations?|can see|see your|your (screen|video|audio)|recording|breakout|polls?|whiteboard|host controls?)\b/i;
  const okName = (n) => n && n.length >= 2 && n.length <= 40 && n.split(/\s+/).length <= 5 &&
    !isYou(n) && !/[@\d]{3,}/.test(n) && !UI_TEXT.test(n);

  function getOwnName() {
    // 1. Google account button: aria-label="Google Account: Full Name (email)"
    const acct = document.querySelector("[aria-label^='Google Account' i]");
    const m = acct && /Google Account:\s*(.+?)\s*(\(|\n|$)/i.exec(acct.getAttribute("aria-label") || "");
    if (m && okName(m[1].trim())) return m[1].trim();
    // 2. Own tile label (tile that carries data-self-name; real name is a child text)
    const selfTile = document.querySelector("[data-self-name]");
    const root = selfTile && (selfTile.closest("[data-participant-id]") || selfTile.parentElement);
    if (root) {
      for (const el of root.querySelectorAll("span, div")) {
        if (el.children.length) continue;
        const t = el.textContent.trim();
        if (okName(t)) return t;
      }
    }
    return "";
  }

  function collectParticipants() {
    const names = new Set();
    const own = getOwnName();
    if (own) names.add(own);
    document.querySelectorAll("[data-participant-id]").forEach((tile) => {
      if (tile.querySelector("[data-self-name]")) return;
      // Try the name element(s) first, then fall back to any leaf text. Both are filtered through okName().
      const pickName = (selector) => {
        for (const el of tile.querySelectorAll(selector)) {
          if (el.children.length) continue;
          const t = el.textContent.trim();
          if (okName(t) && /^[\p{L}][\p{L}\s.'-]*$/u.test(t)) return t;
        }
        return "";
      };
      const name = pickName("span.notranslate, div.notranslate") || pickName("span, div");
      if (name) names.add(name);
    });
    return Array.from(names);
  }

  function sendParticipants() {
    const names = collectParticipants();
    if (names.length) {
      safeSendMessage({ type: "meet-participants", names });
    }
  }
  participantInterval = setInterval(sendParticipants, 3000);
  setTimeout(sendParticipants, 1000);

  // ── Main extraction routine ───────────────────────────────────────────────
  function extractCaptions() {
    // 1. Find all active caption text elements in Google Meet
    let textContainers = Array.from(
      document.querySelectorAll(".iTTPOb, [jsname='YSxPC']")
    );

    if (textContainers.length === 0) {
      textContainers = Array.from(
        document.querySelectorAll(".TBMuR, .cnhedtc, .ePMEFc, .bj4p3b")
      );
    }

    if (textContainers.length === 0) {
      // Fallback: Meet's caption region is exposed via ARIA, independent of class names
      const region = document.querySelector("[role='region'][aria-label*='aption' i], [aria-live='polite'][class*='caption' i]");
      if (region) {
        textContainers = Array.from(region.querySelectorAll("div, span")).filter(
          (el) => !el.children.length && el.textContent.trim().length >= 2 &&
                  !(el.closest("button") || el.closest("[role='button']"))
        );
      }
    }

    if (textContainers.length === 0) {
      if (!window.__mmcWarned) { window.__mmcWarned = true; log("No caption elements found - are captions (CC) turned on?"); }
      return;
    }

    // Filter to elements that actually contain speech text (> 2 chars)
    // and avoid nested duplicates (keep inner leaf)
    const activeTextContainers = textContainers.filter((el) => {
      const txt = el.textContent.trim();
      return txt.length >= 2 && !el.querySelector(".iTTPOb, [jsname='YSxPC']");
    });

    if (activeTextContainers.length === 0) return;

    const turns = [];

    // 2. Isolate each text container's specific turn row
    for (const textContainer of activeTextContainers) {
      const speechText = textContainer.textContent.trim();
      if (!speechText) continue;

      // Walk up to find the specific turn row that contains ONLY this text container
      // (Stop as soon as the parent would contain other activeTextContainers)
      let turnRow = textContainer;
      let depth = 0;

      while (turnRow.parentElement && turnRow.parentElement !== document.body && depth < 3) {
        const parent = turnRow.parentElement;
        const containsOther = activeTextContainers.some(
          (other) => other !== textContainer && parent.contains(other)
        );
        if (containsOther) {
          // Parent is the shared multi-turn container! Stop here so turnRow is isolated.
          break;
        }
        if (turnRow.classList.contains("nMx2nd") || turnRow.getAttribute("jsname") === "bViI2b") {
          break;
        }
        turnRow = parent;
        depth++;
      }

      // 3. Extract speaker for this isolated turn row
      let speaker = findSpeakerInRow(turnRow, speechText);
      speaker = (speaker || "You").trim();
      if (/^you$/i.test(speaker)) speaker = getOwnName() || "You";

      // Clean speech text: if it starts with the speaker name, remove duplicate prefix
      let cleanedSpeech = speechText;
      const prefixRegex = new RegExp(`^${escapeRegex(speaker)}\\s*[:\\-]?\\s*`, "i");
      cleanedSpeech = cleanedSpeech.replace(prefixRegex, "").trim();
      if (!cleanedSpeech) cleanedSpeech = speechText;

      if (!turnRow._mmcTurnId) {
        turnRow._mmcTurnId = `turn-${nextTurnId++}`;
      }

      turns.push({
        turnId: turnRow._mmcTurnId,
        speaker: speaker,
        text: cleanedSpeech,
      });
    }

    // ── Dispatch all turns ──────────────────────────────────────────────────
    for (const turn of turns) {
      const lastText = recentTurns.get(turn.turnId);
      if (lastText !== turn.text) {
        recentTurns.set(turn.turnId, turn.text);

        const formatted = `${turn.speaker}: ${turn.text}`;
        log(`[${turn.turnId}] ${turn.speaker}: ${turn.text.slice(0, 45)}...`);

        safeSendMessage({
          type: "meet-caption",
          turnId: turn.turnId,
          speaker: turn.speaker,
          text: formatted,
          rawText: turn.text,
          timestamp: new Date().toLocaleTimeString("en-IN", {
            timeZone: "Asia/Kolkata",
            hour12: false,
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
        });
      }
    }
  }

  // ── Debounced observer ───────────────────────────────────────────────────
  function onMutation() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(extractCaptions, 120);
  }

  observer = new MutationObserver(onMutation);
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  window.__mmcCaptionsObserver = observer;

  // Heartbeat watchdog (1.5 s)
  watchdogInterval = setInterval(extractCaptions, 1500);

  safeSendMessage({ type: "meet-captions-ready" });
  log("Speaker-isolated caption extractor ready.");
})();
