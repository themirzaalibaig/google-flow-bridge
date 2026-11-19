/**
 * Google Flow Bridge - Content Script
 * Runs directly on flow.google.com to control UI settings, extract media, and observe DOM
 */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Helper: Open Settings Popover if not already open
async function openSettings() {
  let settings = document.querySelector('flow-prompt-box-settings');
  if (!settings) {
    const trigger = document.querySelector('button[aria-label="Settings trigger"]');
    if (trigger) {
      trigger.click();
      await sleep(300);
    }
  }
  return document.querySelector('flow-prompt-box-settings');
}

// Helper: Close Settings Popover
async function closeSettings() {
  const settings = document.querySelector('flow-prompt-box-settings');
  if (settings) {
    const trigger = document.querySelector('button[aria-label="Settings trigger"]');
    if (trigger) {
      trigger.click();
      await sleep(200);
    }
  }
}

// Set Generation Mode: "image" | "video"
async function setMode(mode) {
  await openSettings();
  const btns = Array.from(document.querySelectorAll('flow-prompt-box-settings button[role="radio"]'));
  const target = btns.find((b) => b.innerText.toLowerCase().includes(mode.toLowerCase()));
  if (target) {
    target.click();
    await sleep(200);
    return true;
  }
  return false;
}

// Set Aspect Ratio: "16:9" | "4:3" | "1:1" | "3:4" | "9:16"
async function setAspectRatio(ratio) {
  await openSettings();
  const btns = Array.from(document.querySelectorAll('flow-prompt-box-settings button[role="radio"]'));
  const target = btns.find((b) => b.innerText.includes(ratio));
  if (target) {
    target.click();
    await sleep(200);
    return true;
  }
  return false;
}

// Set Generation Count: "1" | "2" | "3" | "4"
async function setCount(count) {
  await openSettings();
  const tag = String(count).startsWith('x') ? String(count) : 'x' + count;
  const btns = Array.from(document.querySelectorAll('flow-prompt-box-settings button[role="radio"]'));
  const target = btns.find((b) => b.innerText.trim() === tag);
  if (target) {
    target.click();
    await sleep(200);
    return true;
  }
  return false;
}

// Select Model Family
async function setModel(modelKeyword) {
  await openSettings();
  const modelDropdown = document.querySelector('button[aria-label="Select model family"]');
  if (modelDropdown) {
    modelDropdown.click();
    await sleep(250);
    const menuItems = Array.from(document.querySelectorAll('[role="menuitem"]'));
    const target = menuItems.find((m) =>
      m.innerText.toLowerCase().includes(modelKeyword.toLowerCase())
    );
    if (target) {
      target.click();
      await sleep(200);
      return true;
    }
  }
  return false;
}

// Set Agent Mode: true | false
async function setAgentMode(enabled) {
  const agentBtn = Array.from(document.querySelectorAll('button')).find(
    (b) => b.innerText.trim() === 'Agent'
  );
  if (agentBtn) {
    const isPressed = agentBtn.getAttribute('aria-pressed') === 'true';
    if (isPressed !== enabled) {
      agentBtn.click();
      await sleep(200);
    }
    return true;
  }
  return false;
}

// Focus Prompt Input Box
function focusAndClearPromptBox() {
  const promptInput = document.querySelector('[aria-label="What do you want to create?"]');
  if (promptInput) {
    promptInput.focus();
    const clearBtn = document.querySelector('button[aria-label="Clear prompt"]');
    if (clearBtn) {
      clearBtn.click();
    }
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(promptInput);
    sel.removeAllRanges();
    sel.addRange(range);
    return true;
  }
  return false;
}

// Trigger "Start Generation" button
async function triggerStartGeneration() {
  const startBtn = document.querySelector('button[aria-label="Start generation"]');
  if (startBtn && !startBtn.disabled) {
    startBtn.click();
    return true;
  }
  return false;
}

// Get current state of all controls
function getUIState() {
  const trigger = document.querySelector('button[aria-label="Settings trigger"]');
  const agentBtn = Array.from(document.querySelectorAll('button')).find(
    (b) => b.innerText.trim() === 'Agent'
  );
  const promptInput = document.querySelector('[aria-label="What do you want to create?"]');
  const startBtn = document.querySelector('button[aria-label="Start generation"]');

  return {
    url: window.location.href,
    triggerText: trigger ? trigger.innerText.replace(/\n+/g, ' ') : null,
    agentMode: agentBtn ? agentBtn.getAttribute('aria-pressed') === 'true' : false,
    promptText: promptInput ? promptInput.innerText.trim() : '',
    canGenerate: startBtn ? !startBtn.disabled : false,
    timestamp: Date.now()
  };
}

// Collect existing media URLs currently in the project
function getExistingMediaUrls() {
  const images = Array.from(document.querySelectorAll('img')).map((img) => img.src);
  const videos = Array.from(document.querySelectorAll('video')).map((v) => v.src);
  return {
    images: Array.from(new Set(images.filter((src) => src && (src.includes('flow-content.google') || src.includes('flow.google.com/asb/'))))),
    videos: Array.from(new Set(videos.filter((src) => src && src.startsWith('http'))))
  };
}

// Keep-alive port to background service worker
let keepAlivePort = null;
function setupKeepAlive() {
  try {
    keepAlivePort = chrome.runtime.connect({ name: 'keepAlive' });
    keepAlivePort.onDisconnect.addListener(() => {
      setTimeout(setupKeepAlive, 1000);
    });
  } catch (e) {}
}
setupKeepAlive();
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  (async () => {
    try {
      const action = request.action;
      if (action === 'get_state') {
        sendResponse({ success: true, state: getUIState() });
      } else if (action === 'get_media') {
        sendResponse({ success: true, media: getExistingMediaUrls() });
      } else if (action === 'focus_prompt') {
        const ok = focusAndClearPromptBox();
        sendResponse({ success: ok });
      } else if (action === 'configure') {
        const { mode, aspectRatio, count, model, agentMode } = request;
        if (mode) await setMode(mode);
        if (aspectRatio) await setAspectRatio(aspectRatio);
        if (count) await setCount(count);
        if (model) await setModel(model);
        if (typeof agentMode === 'boolean') await setAgentMode(agentMode);
        await closeSettings();
        sendResponse({ success: true, state: getUIState() });
      } else if (action === 'start_generation') {
        const started = await triggerStartGeneration();
        sendResponse({ success: started, state: getUIState() });
      } else {
        sendResponse({ success: false, error: 'Unknown action: ' + action });
      }
    } catch (err) {
      sendResponse({ success: false, error: err.toString() });
    }
  })();
  return true; // Keep response channel open for async
});
