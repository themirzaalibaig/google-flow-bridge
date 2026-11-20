/**
 * Google Flow Bridge - Background Service Worker
 * Manages WebSocket/HTTP connection with local Python Server,
 * controls Chrome Debugger for native text typing, and routes automation requests.
 */

const LOCAL_SERVER_WS = 'ws://127.0.0.1:8776/ws';
let ws = null;
let reconnectTimer = null;

// Find active or open Google Flow tab without forcing window focus/fullscreen
async function getFlowTab() {
  const tabs = await chrome.tabs.query({ url: ['https://flow.google.com/*', 'https://labs.google/fx/tools/flow/*'] });
  if (tabs.length > 0) {
    return tabs[0];
  }

  // Auto-open Google Flow in a compact side window if not open
  console.log('[Google Flow Bridge] No Flow tab found. Opening https://flow.google.com/ in a compact window...');
  const newWin = await chrome.windows.create({
    url: 'https://flow.google.com/',
    type: 'popup',
    width: 900,
    height: 700,
    focused: false
  });

  const newTab = newWin.tabs?.[0];
  if (newTab) {
    await new Promise((resolve) => {
      const listener = (tabId, changeInfo) => {
        if (tabId === newTab.id && changeInfo.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(listener);
          setTimeout(resolve, 2500);
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
      setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }, 15000);
    });
    return newTab;
  }

  const fallbackTabs = await chrome.tabs.query({ url: ['https://flow.google.com/*', 'https://labs.google/fx/tools/flow/*'] });
  return fallbackTabs[0] || null;
}

// Safe message sender to tabs with retry and auto-injection
async function sendTabMessageSafe(tabId, message, retries = 3) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message);
      return response;
    } catch (err) {
      const errStr = err?.message || String(err);
      if (errStr.includes('Receiving end does not exist') || errStr.includes('Could not establish connection')) {
        // Content script might need to be re-injected if the tab was loaded before extension reload
        try {
          await chrome.scripting.executeScript({
            target: { tabId },
            files: ['content.js']
          });
          await new Promise(r => setTimeout(r, 400));
        } catch (injErr) {}
      }
      if (attempt === retries) throw err;
      await new Promise(r => setTimeout(r, 600));
    }
  }
}
async function sendCdpCommand(tabId, method, params = {}) {
  const target = { tabId };
  try {
    await chrome.debugger.attach(target, '1.3');
  } catch (e) {
    // Already attached or ignorable error
  }
  const result = await chrome.debugger.sendCommand(target, method, params);
  return result;
}

// Helper to safely run CDP commands with robust attach & error handling
async function runWithDebugger(tabId, fn) {
  const target = { tabId };
  let attached = false;
  let lastError = null;
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      await chrome.debugger.attach(target, '1.3');
      attached = true;
      break;
    } catch (e) {
      lastError = e;
      const errStr = e?.message || String(e);
      if (errStr.includes('already attached') || errStr.includes('Another debugger is already attached')) {
        try { await chrome.debugger.detach(target); } catch (_) {}
      }
      await new Promise(r => setTimeout(r, 400));
    }
  }

  if (!attached) {
    throw new Error(`Failed to attach debugger to tab ${tabId}: ${lastError?.message || lastError}`);
  }

  try {
    return await fn(target);
  } finally {
    try { await chrome.debugger.detach(target); } catch (_) {}
  }
}

// Type text natively via Chrome Debugger Input.insertText
async function typeTextViaDebugger(tabId, text) {
  return await runWithDebugger(tabId, async (target) => {
    // Clear existing prompt text first by sending Backspace after selection
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', {
      type: 'rawKeyDown',
      windowsVirtualKeyCode: 8,
      nativeVirtualKeyCode: 8,
      key: 'Backspace'
    });
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', {
      type: 'keyUp',
      windowsVirtualKeyCode: 8,
      nativeVirtualKeyCode: 8,
      key: 'Backspace'
    });

    // Insert the prompt text natively (bypasses CSP and triggers ProseMirror cleanly)
    await chrome.debugger.sendCommand(target, 'Input.insertText', { text });

    // Wait briefly for ProseMirror and Angular model sync
    await new Promise((r) => setTimeout(r, 400));

    // Press Enter natively to submit the prompt directly
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', {
      type: 'rawKeyDown',
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13,
      key: 'Enter',
      code: 'Enter'
    });
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', {
      type: 'keyUp',
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13,
      key: 'Enter',
      code: 'Enter'
    });
  });
}

// Execute complete generation request
async function handleGenerateRequest(payload) {
  const tab = await getFlowTab();
  if (!tab) {
    throw new Error('No open Google Flow tab found. Please open https://flow.google.com/ in Chrome.');
  }

  // 1. Get initial media before generation
  const initialMediaRes = await sendTabMessageSafe(tab.id, { action: 'get_media' }).catch(() => null);
  const oldImages = new Set((initialMediaRes && initialMediaRes.media && initialMediaRes.media.images) || []);

  // 2. Configure UI settings (mode, aspect ratio, model, count, agent mode)
  const configRes = await sendTabMessageSafe(tab.id, {
    action: 'configure',
    mode: payload.mode || (payload.type === 'video' ? 'video' : 'image'),
    aspectRatio: payload.aspect_ratio || payload.aspectRatio || '1:1',
    count: payload.count || payload.n || 1,
    model: payload.model || (payload.type === 'video' ? 'Veo' : 'Banana'),
    agentMode: typeof payload.agent_mode === 'boolean' ? payload.agent_mode : false
  }).catch(() => null);

  // 3. Focus prompt box
  await sendTabMessageSafe(tab.id, { action: 'focus_prompt' }).catch(() => null);

  // 4. Type prompt and submit natively via debugger, with in-DOM fallback if debugger is unavailable
  if (payload.prompt) {
    try {
      await typeTextViaDebugger(tab.id, payload.prompt);
    } catch (dbgErr) {
      console.warn('[Google Flow Bridge] Debugger attach failed in background, using in-DOM fallback:', dbgErr);
      await sendTabMessageSafe(tab.id, { action: 'inject_and_submit', prompt: payload.prompt });
    }
  }

  // 5. Short pause then check if generation started, if not click start button as fallback
  await new Promise((r) => setTimeout(r, 1000));
  await sendTabMessageSafe(tab.id, { action: 'start_generation' }).catch(() => null);

  // 6. Poll for new media to appear (timeout 90s)
  const startTime = Date.now();
  let newMediaUrls = [];
  while (Date.now() - startTime < 90000) {
    await new Promise((r) => setTimeout(r, 2000));
    const curMediaRes = await sendTabMessageSafe(tab.id, { action: 'get_media' }).catch(() => null);
    const curImages = (curMediaRes && curMediaRes.media && curMediaRes.media.images) || [];
    newMediaUrls = curImages.filter((url) => !oldImages.has(url));
    if (newMediaUrls.length > 0) {
      break;
    }
  }

  return {
    success: true,
    new_media: newMediaUrls,
    media_url: newMediaUrls[0] || null,
    settings: configRes ? configRes.state : null
  };
}

// WebSocket Connection to Local Python Server
function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  try {
    ws = new WebSocket(LOCAL_SERVER_WS);

    ws.onopen = async () => {
      console.log('[Google Flow Bridge] Connected to local Python server');
      if (reconnectTimer) {
        clearInterval(reconnectTimer);
        reconnectTimer = null;
      }

      // Query active account identity and register with server
      try {
        const tab = await getFlowTab();
        let email = null;
        let url = null;
        if (tab) {
          url = tab.url;
          const stateRes = await chrome.tabs.sendMessage(tab.id, { action: 'get_state' }).catch(() => null);
          email = stateRes?.state?.email || null;
        }
        ws.send(JSON.stringify({
          action: 'register',
          payload: {
            email: email,
            url: url,
            extensionVersion: chrome.runtime.getManifest().version
          }
        }));
      } catch (e) {}
    };

    ws.onmessage = async (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        return;
      }

      const { id, action, payload } = data;
      try {
        if (action === 'generate') {
          const result = await handleGenerateRequest(payload);
          ws.send(JSON.stringify({ id, success: true, result }));
        } else if (action === 'get_state') {
          const tab = await getFlowTab();
          if (!tab) {
            ws.send(JSON.stringify({ id, success: false, error: 'No Flow tab found' }));
            return;
          }
          const stateRes = await chrome.tabs.sendMessage(tab.id, { action: 'get_state' });
          ws.send(JSON.stringify({ id, success: true, result: stateRes }));
        } else if (action === 'evaluate') {
          const tab = await getFlowTab();
          if (!tab) {
            ws.send(JSON.stringify({ id, success: false, error: 'No Flow tab found' }));
            return;
          }
          const target = { tabId: tab.id };
          let attached = false;
          try {
            await chrome.debugger.attach(target, '1.3');
            attached = true;
          } catch (e) {}
          try {
            const evalRes = await chrome.debugger.sendCommand(target, 'Runtime.evaluate', {
              expression: payload.script,
              returnByValue: true,
              awaitPromise: true
            });
            ws.send(JSON.stringify({ id, success: true, result: evalRes.result?.value || evalRes.result }));
          } catch (err) {
            ws.send(JSON.stringify({ id, success: false, error: err.message || err.toString() }));
          } finally {
            if (attached) {
              try { await chrome.debugger.detach(target); } catch (e) {}
            }
          }
        } else if (['get_balance', 'list_projects', 'create_project', 'select_project', 'attach_ingredient', 'download_media', 'inspect_dom', 'click_ref', 'type_ref'].includes(action)) {
          const tab = await getFlowTab();
          if (!tab) {
            ws.send(JSON.stringify({ id, success: false, error: 'No Flow tab found' }));
            return;
          }
          const forwardRes = await sendTabMessageSafe(tab.id, { action, ...payload });
          ws.send(JSON.stringify({ id, success: true, result: forwardRes }));
        } else {
          ws.send(JSON.stringify({ id, success: false, error: 'Unsupported action' }));
        }
      } catch (err) {
        ws.send(JSON.stringify({ id, success: false, error: err.message || err.toString() }));
      }
    };

    ws.onclose = () => {
      console.log('[Google Flow Bridge] Disconnected from Python server, retrying in 3s...');
      scheduleReconnect();
    };

    ws.onerror = () => {
      ws.close();
    };
  } catch (e) {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (!reconnectTimer) {
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  }
}

// Keep service worker alive with alarm and reconnect
chrome.alarms.create('keepAlive', { periodInMinutes: 0.2 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepAlive') {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectWebSocket();
    }
  }
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'keepAlive') {
    port.onDisconnect.addListener(() => {
      // disconnected
    });
  }
});

// Start connection on service worker launch
connectWebSocket();
