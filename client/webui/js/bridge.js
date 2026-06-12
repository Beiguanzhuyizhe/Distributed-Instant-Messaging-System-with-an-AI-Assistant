/**
 * bridge.js — Python ↔ JS 双向通信适配层
 *
 * Python 端通过 pywebview.js_api 暴露方法，JS 调用 pywebview.api.method()
 * Python 通过 evaluate_js("window.__pyEvent(...)") 推送事件给 JS
 */

(function () {
  'use strict';

  // ============================================================
  // 事件系统
  // ============================================================

  const _listeners = {};

  /**
   * Python 调用此函数推送事件
   * @param {string} jsonStr - JSON 序列化的事件对象 {type, data}
   */
  window.__pyEvent = function (jsonStr) {
    let event;
    try {
      event = typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
    } catch (e) {
      console.warn('[Bridge] Invalid JSON from Python:', jsonStr);
      return;
    }
    const type = event.type;
    const data = event.data;
    const handlers = _listeners[type];
    if (handlers) {
      handlers.forEach(function (fn) {
        try {
          fn(data);
        } catch (e) {
          console.warn('[Bridge] Event handler error:', type, e);
        }
      });
    }
    // 通用 * 监听器
    const allHandlers = _listeners['*'];
    if (allHandlers) {
      allHandlers.forEach(function (fn) {
        try {
          fn(type, data);
        } catch (e) {
          console.warn('[Bridge] Wildcard handler error:', e);
        }
      });
    }
  };

  /**
   * 注册事件监听器
   * @param {string} type - 事件类型（'*' 表示所有事件）
   * @param {function} fn - 回调
   */
  function on(type, fn) {
    if (!_listeners[type]) {
      _listeners[type] = [];
    }
    _listeners[type].push(fn);
    return function () {
      var idx = _listeners[type].indexOf(fn);
      if (idx >= 0) _listeners[type].splice(idx, 1);
    };
  }

  /**
   * 一次性监听
   */
  function once(type, fn) {
    var wrapped = function (data) {
      fn(data);
      var idx = _listeners[type].indexOf(wrapped);
      if (idx >= 0) _listeners[type].splice(idx, 1);
    };
    return on(type, wrapped);
  }

  // ============================================================
  // API 调用封装
  // ============================================================

  var api = null;

  /**
   * 检测 pywebview.api 是否就绪
   */
  function isReady() {
    return typeof pywebview !== 'undefined' && pywebview.api;
  }

  /**
   * 等待 API 就绪
   */
  function ready() {
    return new Promise(function (resolve) {
      if (isReady()) {
        api = pywebview.api;
        resolve(api);
        return;
      }
      var check = setInterval(function () {
        if (isReady()) {
          clearInterval(check);
          api = pywebview.api;
          resolve(api);
        }
      }, 100);
      // 超时 10 秒
      setTimeout(function () {
        clearInterval(check);
        if (!api) {
          console.warn('[Bridge] pywebview API not available after 10s');
        }
        resolve(api);
      }, 10000);
    });
  }

  /**
   * 调用 Python 方法
   * @param {string} method - 方法名
   * @param {...any} args - 参数
   */
  function call(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    if (api && api[method]) {
      try {
        return api[method].apply(api, args);
      } catch (e) {
        console.warn('[Bridge] Call error:', method, e);
        return Promise.reject(e);
      }
    }
    console.warn('[Bridge] Method not found:', method);
    return Promise.reject(new Error('Method not found: ' + method));
  }

  // ============================================================
  // 高级 API 封装
  // ============================================================

  var Bridge = {
    on: on,
    once: once,
    ready: ready,
    call: call,

    // --- 认证 ---
    login: function (username, password) {
      return call('login', username, password);
    },
    register: function (username, password) {
      return call('register', username, password);
    },

    // --- 消息 ---
    sendPrivateMsg: function (targetId, content) {
      return call('send_private_msg', targetId, content);
    },
    sendGroupMsg: function (groupId, content) {
      return call('send_group_msg', groupId, content);
    },
    sendAiQuery: function (query, groupId) {
      return call('send_ai_query', query, groupId || 0);
    },
    sendRecall: function (msgId) {
      return call('send_recall', msgId);
    },

    // --- 历史 & 在线 ---
    requestHistory: function (targetType, targetId) {
      return call('request_history', targetType, targetId);
    },
    requestOnlineUsers: function () {
      return call('request_online_users');
    },

    // --- 群组 ---
    groupCreate: function (name) {
      return call('group_create', name);
    },
    groupJoin: function (groupId) {
      return call('group_join', groupId);
    },
    groupLeave: function (groupId) {
      return call('group_leave', groupId);
    },

    // --- 文件 ---
    selectAndSendFile: function () {
      return call('select_and_send_file');
    },

    // --- 状态 ---
    setCurrentTarget: function (name, id, type) {
      return call('set_current_target', name, id, type);
    },
    getInitialState: function () {
      return call('get_initial_state');
    },
    getConnectionStatus: function () {
      return call('get_connection_status');
    },
    getOnlineUsersSnapshot: function () {
      return call('get_online_users_snapshot');
    },
  };

  window.Bridge = Bridge;
})();
