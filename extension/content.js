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

// Get user account balance and subscription info
async function getAccountBalance() {
  const accountBtn = document.querySelector('button[aria-label*="Google Account"], [aria-label*="Account details"]');
  if (accountBtn) {
    accountBtn.click();
    await sleep(400);
    const dialogOrMenu = document.querySelector('[role="menu"], [role="dialog"], flow-user-menu, .mat-mdc-menu-panel');
    const text = dialogOrMenu ? dialogOrMenu.innerText : '';
    
    // Extract credits (e.g. "1,020 Google Flow credits")
    const match = text.match(/([\d,]+)\s*(?:Google\s*Flow\s*)?credits/i);
    const resetMatch = text.match(/Resets\s+([^\n]+)/i);

    // Close menu
    document.body.click();

    return {
      credits: match ? parseInt(match[1].replace(/,/g, ''), 10) : null,
      creditsRaw: match ? match[0] : null,
      resets: resetMatch ? resetMatch[1].trim() : null,
      email: text.match(/[\w.-]+@[\w.-]+\.\w+/)?.[0] || null
    };
  }
  return { credits: null, error: 'Account button not found' };
}

// List user projects from home page
async function listProjects() {
  // If not on home page, we can inspect current project or navigate
  if (!window.location.pathname.startsWith('/project/')) {
    const cards = Array.from(document.querySelectorAll('a[href*="/project/"]')).map(a => ({
      url: a.href,
      id: a.href.split('/project/')[1]?.split('/')[0] || '',
      title: a.innerText.trim() || a.getAttribute('aria-label') || 'Untitled'
    }));
    return cards.filter(c => c.id);
  }
  return [{
    url: window.location.href,
    id: window.location.pathname.split('/project/')[1]?.split('/')[0] || '',
    title: document.querySelector('input[aria-label="Editable text"]')?.value || document.title,
    current: true
  }];
}

// Create new project
async function createNewProject() {
  if (window.location.pathname !== '/') {
    window.location.href = 'https://flow.google.com/';
    await sleep(2500);
  }
  const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('New project'));
  if (btn) {
    btn.click();
    await sleep(2500);
    return { success: true, url: window.location.href };
  }
  return { success: false, error: 'New project button not found' };
}

// Select/Switch to project by ID
async function selectProject(projectId) {
  const targetUrl = `https://flow.google.com/project/${projectId}`;
  window.location.href = targetUrl;
  await sleep(2000);
  return { success: true, url: window.location.href };
}

// Attach media as ingredient to the prompt
async function attachMediaIngredient(assetKeyword) {
  const ingredientsBtn = document.querySelector('button[aria-label="Add ingredients to the prompt box"]');
  if (ingredientsBtn) {
    ingredientsBtn.click();
    await sleep(800);

    const assetOptions = Array.from(document.querySelectorAll('[role="option"], flow-asset-item, .asset-card'));
    const target = assetOptions.find(opt => 
      !assetKeyword || (opt.innerText && opt.innerText.toLowerCase().includes(assetKeyword.toLowerCase()))
    );

    if (target) {
      target.click();
      await sleep(400);

      const addBtn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Add to prompt'));
      if (addBtn) {
        addBtn.click();
        await sleep(500);
        return { success: true, attached: target.innerText.replace(/\n+/g, ' ').trim() };
      }
    }
  }
  return { success: false, error: 'Failed to attach ingredient' };
}
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
      } else if (action === 'get_balance') {
        const balance = await getAccountBalance();
        sendResponse({ success: true, balance });
      } else if (action === 'list_projects') {
        const projects = await listProjects();
        sendResponse({ success: true, projects });
      } else if (action === 'create_project') {
        const res = await createNewProject();
        sendResponse(res);
      } else if (action === 'select_project') {
        const res = await selectProject(request.projectId);
        sendResponse(res);
      } else if (action === 'attach_ingredient') {
        const res = await attachMediaIngredient(request.assetKeyword);
        sendResponse(res);
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
