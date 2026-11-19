/**
 * Google Flow Bridge - Background Service Worker
 * Manages WebSocket/HTTP connection with local Python Server,
 * controls Chrome Debugger for native text typing, and routes automation requests.
 */

const LOCAL_SERVER_WS = 'ws://127.0.0.1:8769/ws';
let ws = null;
let reconnectTimer = null;

// Find active or open Google Flow tab
async function getFlowTab() {
  const tabs = await chrome.tabs.query({ url: ['https://flow.google.com/*', 'https://labs.google/fx/tools/flow/*'] });
  if (tabs.length > 0) {
    return tabs[0];
  }
  return null;
}

// Send CDP command with automatic attach/detach
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

// Type text natively via Chrome Debugger Input.insertText
async function typeTextViaDebugger(tabId, text) {
  const target = { tabId };
  try {
    await chrome.debugger.attach(target, '1.3');
  } catch (e) {
    // Might already be attached
  }

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

  // Detach debugger so user doesn't see debugger banner
  try {
    await chrome.debugger.detach(target);
  } catch (e) {}
}

// Execute complete generation request
async function handleGenerateRequest(payload) {
  const tab = await getFlowTab();
  if (!tab) {
    throw new Error('No open Google Flow tab found. Please open https://flow.google.com/ in Chrome.');
  }

  // 1. Get initial media before generation
  const initialMediaRes = await chrome.tabs.sendMessage(tab.id, { action: 'get_media' });
  const oldImages = new Set((initialMediaRes && initialMediaRes.media && initialMediaRes.media.images) || []);

  // 2. Configure UI settings (mode, aspect ratio, model, count, agent mode)
  const configRes = await chrome.tabs.sendMessage(tab.id, {
    action: 'configure',
    mode: payload.mode || (payload.type === 'video' ? 'video' : 'image'),
    aspectRatio: payload.aspect_ratio || payload.aspectRatio || '1:1',
    count: payload.count || payload.n || 1,
    model: payload.model || (payload.type === 'video' ? 'Veo' : 'Banana'),
    agentMode: typeof payload.agent_mode === 'boolean' ? payload.agent_mode : false
  });

  // 3. Focus prompt box
  await chrome.tabs.sendMessage(tab.id, { action: 'focus_prompt' });

  // 4. Type prompt and submit natively via debugger
  if (payload.prompt) {
    await typeTextViaDebugger(tab.id, payload.prompt);
  }

  // 5. Short pause then check if generation started, if not click start button as fallback
  await new Promise((r) => setTimeout(r, 1000));
  await chrome.tabs.sendMessage(tab.id, { action: 'start_generation' });

  // 6. Poll for new media to appear (timeout 90s)
  const startTime = Date.now();
  let newMediaUrls = [];
  while (Date.now() - startTime < 90000) {
    await new Promise((r) => setTimeout(r, 2000));
    const curMediaRes = await chrome.tabs.sendMessage(tab.id, { action: 'get_media' });
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

    ws.onopen = () => {
      console.log('[Google Flow Bridge] Connected to local Python server');
      if (reconnectTimer) {
        clearInterval(reconnectTimer);
        reconnectTimer = null;
      }
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
        } else if (['get_balance', 'list_projects', 'create_project', 'select_project', 'attach_ingredient', 'inspect_dom', 'click_ref', 'type_ref'].includes(action)) {
          const tab = await getFlowTab();
          if (!tab) {
            ws.send(JSON.stringify({ id, success: false, error: 'No Flow tab found' }));
            return;
          }
          const forwardRes = await chrome.tabs.sendMessage(tab.id, { action, ...payload });
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
