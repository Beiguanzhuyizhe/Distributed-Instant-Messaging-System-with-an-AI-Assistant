/**
 * chat.js — 聊天主界面（布局 + 输入栏 + 状态栏 + 动画 + 群组管理）
 */

(function () {
  'use strict';

  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;
  var useCallback = React.useCallback;
  var useMemo = React.useMemo;

  // ---- 头像颜色 ----
  var AVATAR_COLORS = [
    '#6c63ff', '#e91e63', '#ff9800', '#4caf50', '#2196f3',
    '#9c27b0', '#f44336', '#00bcd4', '#ff5722', '#3f51b5',
  ];
  function getAvatarColor(name) {
    if (!name) return AVATAR_COLORS[0];
    var hash = 0;
    for (var i = 0; i < name.length; i++) {
      hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
  }
  function getInitials(name) {
    return name ? name.charAt(0).toUpperCase() : '?';
  }

  // ============================================================
  // 聊天头部
  // ============================================================

  function ChatHeader(props) {
    var targetName = props.targetName;
    var chatType = props.chatType;
    var onlineUsers = props.onlineUsers || {};

    var isAi = chatType === 'ai';
    var isOnline = chatType === 'private' && onlineUsers[targetName];
    var statusText = isAi ? 'Powered by BigModel AI' : (chatType === 'group' ? 'Group' : (isOnline ? 'Online' : 'Offline'));
    var avatarColor = getAvatarColor(isAi ? 'AI' : (targetName || '?'));
    var displayName = targetName;

    if (!targetName) return null;

    return h('div', { className: 'chat-header' + (isAi ? ' ai-header' : ''), ref: function(el) { props.headerRef && props.headerRef(el); } },
      h('div', { className: 'header-avatar' + (isAi ? ' ai-avatar' : ''), style: { background: avatarColor } },
        isAi ? 'AI' : getInitials(targetName)
      ),
      h('div', { className: 'header-info' },
        h('div', { className: 'header-name' },
          displayName,
          isAi && h('span', { className: 'header-ai-tag' }, 'AI'),
        ),
        h('div', { className: 'header-status' }, statusText),
      ),
    );
  }

  // ============================================================
  // 输入栏 — 按钮改为文字
  // ============================================================

  function ChatInput(props) {
    var onSend = props.onSend;
    var onAI = props.onAI;
    var onFile = props.onFile;
    var disabled = props.disabled;

    var _useState = useState(''), text = _useState[0], setText = _useState[1];
    var textareaRef = useRef(null);

    var handleSend = useCallback(function () {
      var content = text.trim();
      if (!content || disabled) return;
      if (content.toUpperCase().startsWith('@AI')) {
        var query = content.substring(3).trim();
        if (query && onAI) onAI(query);
      } else {
        onSend(content);
      }
      setText('');
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }, [text, disabled, onSend, onAI]);

    var handleKeyDown = useCallback(function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    }, [handleSend]);

    var handleInput = useCallback(function (e) {
      setText(e.target.value);
      var el = e.target;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    }, []);

    return h('div', { className: 'input-area' },
      h('div', { className: 'input-container' },
        h('textarea', {
          ref: textareaRef,
          placeholder: disabled ? 'Select a contact to start chatting...' : 'Type a message... (Shift+Enter for new line)',
          value: text,
          onChange: handleInput,
          onKeyDown: handleKeyDown,
          disabled: disabled,
          rows: 1,
        }),
        h('div', { className: 'input-actions' },
          h('button', {
            className: 'input-btn file-btn',
            onClick: onFile,
            disabled: disabled,
            title: 'Upload file',
          }, 'Upload'),
          h('button', {
            className: 'input-btn ai-btn',
            onClick: function () { if (onAI) onAI(''); },
            disabled: disabled,
            title: 'Ask AI assistant',
          }, '@AI'),
          h('button', {
            className: 'input-btn send-btn',
            onClick: handleSend,
            disabled: disabled || !text.trim(),
            title: 'Send message',
          }, 'Send'),
        ),
      ),
    );
  }

  // ============================================================
  // 状态栏
  // ============================================================

  function StatusBar(props) {
    var connected = props.connected;
    var onlineCount = props.onlineCount || 0;
    var groupCount = props.groupCount || 0;
    var host = props.host || '127.0.0.1';
    var port = props.port || '8888';

    return h('div', { className: 'status-bar' },
      h('div', { className: 'status-left' },
        h('span', {
          className: 'status-indicator ' + (connected ? 'connected' : 'disconnected'),
        }),
        h('span', null, connected ? 'Connected' : 'Disconnected'),
        h('span', { style: { color: 'var(--text-muted)' } },
          ' | ' + host + ':' + port
        ),
      ),
      h('div', null,
        h('span', null, 'Online: ' + onlineCount),
        h('span', { style: { margin: '0 8px' } }, '|'),
        h('span', null, 'Groups: ' + groupCount),
      ),
    );
  }

  // ============================================================
  // AI 对话框
  // ============================================================

  function AIDialog(props) {
    var visible = props.visible;
    var onClose = props.onClose;
    var onSubmit = props.onSubmit;

    var _useState2 = useState(''), query = _useState2[0], setQuery = _useState2[1];

    if (!visible) return null;

    function handleSubmit(e) {
      e.preventDefault();
      if (query.trim()) {
        onSubmit(query.trim());
        setQuery('');
        onClose();
      }
    }

    return h('div', { className: 'modal-overlay', onClick: onClose },
      h('div', { className: 'modal-content', onClick: function (e) { e.stopPropagation(); } },
        h('h3', null, 'Ask AI Assistant'),
        h('form', { onSubmit: handleSubmit },
          h('div', { className: 'form-group' },
            h('textarea', {
              value: query,
              onChange: function (e) { setQuery(e.target.value); },
              placeholder: 'Ask anything... (e.g. "What is the weather today?")',
              autoFocus: true,
              rows: 3,
            }),
          ),
          h('div', { className: 'modal-actions' },
            h('button', { type: 'button', className: 'btn btn-ghost', onClick: onClose }, 'Cancel'),
            h('button', { type: 'submit', className: 'btn btn-primary' }, 'Send'),
          ),
        ),
      ),
    );
  }

  // ============================================================
  // 群组对话框
  // ============================================================

  function GroupDialog(props) {
    if (!props.visible) return null;
    return h('div', {
      className: 'group-dialog',
      onClick: props.onClose,
    },
      h('div', {
        className: 'dialog-content',
        onClick: function (e) { e.stopPropagation(); },
      },
        h('h3', null, props.title || 'Group'),
        h('div', { className: 'form-group' },
          h('input', {
            type: props.dialogType === 'join' ? 'number' : 'text',
            placeholder: props.dialogType === 'create'
              ? 'Enter group name...'
              : 'Enter group ID...',
            value: props.value,
            onChange: function (e) { props.onChange(e.target.value); },
            autoFocus: true,
            onKeyDown: function (e) {
              if (e.key === 'Enter') {
                e.preventDefault();
                props.onSubmit();
              }
            },
          }),
        ),
        h('div', { className: 'modal-actions' },
          h('button', { className: 'btn btn-ghost', onClick: props.onClose }, 'Cancel'),
          h('button', { className: 'btn btn-primary', onClick: props.onSubmit }, 'Confirm'),
        ),
      ),
    );
  }

  // ============================================================
  // 主聊天组件
  // ============================================================

  function ChatLayout(props) {
    var username = props.username;
    var initialOnlineUsers = props.initialOnlineUsers || {};
    var initialGroups = props.initialGroups || {};
    var _useState3 = useState([]), messages = _useState3[0], setMessages = _useState3[1];
    var _useState4 = useState(initialOnlineUsers), onlineUsers = _useState4[0], setOnlineUsers = _useState4[1];
    var _useState5 = useState(initialGroups), groups = _useState5[0], setGroups = _useState5[1];
    var _useState6 = useState(null), currentTarget = _useState6[0], setCurrentTarget = _useState6[1];
    var _useState7 = useState(null), currentTargetId = _useState7[0], setCurrentTargetId = _useState7[1];
    var _useState8 = useState('private'), currentChatType = _useState8[0], setCurrentChatType = _useState8[1];
    var _useState9 = useState(true), connected = _useState9[0], setConnected = _useState9[1];
    var _useState15 = useState(null), contextMenu = _useState15[0], setContextMenu = _useState15[1];
    var _useState16 = useState(''), searchQuery = _useState16[0], setSearchQuery = _useState16[1];
    // 未读计数 state
    var _useState17 = useState({}), unreadCounts = _useState17[0], setUnreadCounts = _useState17[1];

    // 对话框状态
    var _useState10 = useState(false), showAiDialog = _useState10[0], setShowAiDialog = _useState10[1];
    var _useState11 = useState(false), showGroupDialog = _useState11[0], setShowGroupDialog = _useState11[1];
    var _useState12 = useState('create'), groupDialogType = _useState12[0], setGroupDialogType = _useState12[1];
    var _useState13 = useState(''), groupDialogValue = _useState13[0], setGroupDialogValue = _useState13[1];
    var _useState14 = useState(''), groupDialogTitle = _useState14[0], setGroupDialogTitle = _useState14[1];

    // Refs for GSAP animations
    var chatMainRef = useRef(null);
    var sidebarRef = useRef(null);
    var headerRef = useRef(null);
    var inputRef = useRef(null);
    var welcomeRef = useRef(null);
    var prevTargetRef = useRef(null);

    // ---- Animation: 页面入场 ----
    useEffect(function () {
      if (typeof gsap !== 'undefined') {
        // 侧边栏从左侧滑入
        if (sidebarRef.current) {
          gsap.fromTo(sidebarRef.current,
            { x: -30, opacity: 0 },
            { x: 0, opacity: 1, duration: 0.5, ease: 'power3.out' }
          );
        }
        // 主内容区淡入
        if (chatMainRef.current) {
          gsap.fromTo(chatMainRef.current,
            { opacity: 0, y: 10 },
            { opacity: 1, y: 0, duration: 0.4, ease: 'power2.out', delay: 0.1 }
          );
        }
        // 状态栏从下方滑入
        var statusBar = document.querySelector('.status-bar');
        if (statusBar) {
          gsap.fromTo(statusBar,
            { y: 20, opacity: 0 },
            { y: 0, opacity: 1, duration: 0.3, ease: 'power2.out', delay: 0.2 }
          );
        }
      }
    }, []);

    // ---- Animation: 切换聊天目标（只在 target 真正改变时触发一次） ----
    useEffect(function () {
      if (!currentTarget) return;
      if (prevTargetRef.current === currentTarget) return;
      prevTargetRef.current = currentTarget;

      if (typeof gsap !== 'undefined') {
        // 先杀掉元素上所有 GSAP 动画，防止冲突
        if (headerRef.current) {
          gsap.killTweensOf(headerRef.current);
          gsap.fromTo(headerRef.current,
            { y: -10, opacity: 0 },
            { y: 0, opacity: 1, duration: 0.3, ease: 'power2.out' }
          );
        }
        if (inputRef.current) {
          gsap.killTweensOf(inputRef.current);
          gsap.fromTo(inputRef.current,
            { y: 10, opacity: 0 },
            { y: 0, opacity: 1, duration: 0.3, ease: 'power2.out', delay: 0.1 }
          );
        }
      }
    }, [currentTarget]);

    // ---- Animation: 欢迎界面 ----
    useEffect(function () {
      if (!currentTarget && welcomeRef.current && typeof gsap !== 'undefined') {
        gsap.fromTo(welcomeRef.current,
          { scale: 0.95, opacity: 0 },
          { scale: 1, opacity: 1, duration: 0.5, ease: 'power3.out' }
        );
      }
    }, [currentTarget]);

    // 当前聊天的消息过滤（系统消息 + AI 消息按上下文过滤）
    var filteredMessages = useMemo(function () {
      if (!currentTarget) return [];
      return messages.filter(function (m) {
        // AI 聊天：显示所有 type=ai 的消息
        if (currentChatType === 'ai') {
          return m.type === 'ai';
        }
        if (m.type === 'ai') {
          // AI 响应可以关联到某个聊天（通过 @AI 快捷方式发出的）
          if (currentChatType === 'private' && m.related_type === 'private') {
            return String(m.related_target) === String(currentTargetId);
          }
          if (currentChatType === 'group' && m.related_type === 'group') {
            return String(m.related_target) === String(currentTarget);
          }
          return false;
        }
        if (m.type === 'system') {
          // 无上下文 = 全局系统消息，显示在所有聊天
          if (!m.related_target) return true;
          // 有上下文：只在匹配的聊天中显示
          if (currentChatType === 'private' && m.related_type === 'private') {
            return String(m.related_target) === String(currentTargetId) ||
                   String(m.related_target) === String(currentTarget);
          }
          if (currentChatType === 'group' && m.related_type === 'group') {
            return String(m.related_target) === String(currentTarget);
          }
          return false;
        }
        if (currentChatType === 'private') {
          return m.type === 'private' && (
            m.sender === currentTarget ||
            m.from_id === currentTargetId ||
            (m.type === 'private' && !m.group_id &&
              (m.sender === username || m.sender === currentTarget))
          );
        } else {
          return m.group_id === currentTarget || m.target_id === currentTarget;
        }
      });
    }, [messages, currentTarget, currentTargetId, currentChatType, username]);

    // 挂载时主动拉取当前在线用户/群组状态（解决事件丢失问题）
    useEffect(function () {
      window.Bridge.getOnlineUsersSnapshot().then(function (data) {
        if (!data) return;
        if (data.online_users && Object.keys(data.online_users).length > 0) {
          setOnlineUsers(data.online_users);
        }
        if (data.groups) setGroups(data.groups);
      }).catch(function () {});
      // 同时发起一次新的请求，确保拿到最新数据
      window.Bridge.requestOnlineUsers();
    }, []);

    // 监听 Python 事件
    useEffect(function () {
      var unsubs = [];

      unsubs.push(window.Bridge.on('new_message', function (data) {
        setMessages(function (prev) { return prev.concat([data]); });
        // 未读计数：非当前聊天的新消息才计数
        var isCurrent = false;
        if (data.type === 'private') {
          isCurrent = currentChatType === 'private' &&
            (String(data.from_id) === String(currentTargetId) || data.sender === currentTarget);
        } else if (data.type === 'group') {
          isCurrent = currentChatType === 'group' && String(data.group_id) === String(currentTarget);
        } else if (data.type === 'ai') {
          isCurrent = currentChatType === 'ai';
        }
        if (!isCurrent) {
          setUnreadCounts(function (prev) {
            var key = data.type === 'group' ? 'group:' + data.group_id :
                      data.type === 'ai' ? 'ai:AI Assistant' :
                      'private:' + (data.sender || data.from_id);
            var next = Object.assign({}, prev);
            next[key] = (next[key] || 0) + 1;
            return next;
          });
        }
      }));

      unsubs.push(window.Bridge.on('online_users', function (data) {
        if (data.online_users) setOnlineUsers(data.online_users);
        if (data.groups) setGroups(data.groups);
      }));

      unsubs.push(window.Bridge.on('status_update', function (data) {
        if (data.online_users) setOnlineUsers(data.online_users);
        if (data.groups) setGroups(data.groups);
      }));

      unsubs.push(window.Bridge.on('history', function (data) {
        if (data.messages) {
          setMessages(function (prev) {
            var historyIds = new Set();
            data.messages.forEach(function (hm) {
              if (hm.msg_id) historyIds.add(hm.msg_id);
              if (hm.local_msg_id) historyIds.add(hm.local_msg_id);
            });
            if (data.type === 'private') {
              var kept = prev.filter(function (m) {
                if (m.type !== 'private') return true;
                // 如果这条消息已在历史记录中，去掉本地版本（用历史版本替换）
                if (m.msg_id && historyIds.has(m.msg_id)) return false;
                if (m.server_msg_id && historyIds.has(m.server_msg_id)) return false;
                if (m.local_msg_id && historyIds.has(m.local_msg_id)) return false;
                return true;
              });
              return kept.concat(data.messages);
            } else {
              var filtered = prev.filter(function (m) {
                return m.group_id !== currentTarget;
              });
              return filtered.concat(data.messages);
            }
          });
        }
      }));

      unsubs.push(window.Bridge.on('message_recalled', function (data) {
        setMessages(function (prev) {
          return prev.map(function (m) {
            if (m.msg_id === data.msg_id || m.local_msg_id === data.msg_id || m.server_msg_id === data.msg_id) {
              return Object.assign({}, m, { is_recalled: true, content: '[Message recalled]' });
            }
            return m;
          });
        });
      }));

      unsubs.push(window.Bridge.on('message_acked', function (data) {
        setMessages(function (prev) {
          return prev.map(function (m) {
            if (m.local_msg_id === data.local_msg_id || m.msg_id === data.msg_id) {
              return Object.assign({}, m, { status: 'sent', msg_id: data.msg_id, server_msg_id: data.msg_id, timestamp: data.timestamp || m.timestamp });
            }
            return m;
          });
        });
      }));

      unsubs.push(window.Bridge.on('connection_status', function (data) {
        setConnected(data.status === 'reconnected' || data.status === 'connected');
      }));

      unsubs.push(window.Bridge.on('group_created', function (data) {
        if (data.groups) setGroups(data.groups);
      }));

      unsubs.push(window.Bridge.on('group_joined', function (data) {
        if (data.groups) setGroups(data.groups);
      }));

      unsubs.push(window.Bridge.on('group_left', function (data) {
        if (data.groups) setGroups(data.groups);
        // 如果当前正在查看的群组被退出，清空聊天界面
        if (currentChatType === 'group' && data.group_id && String(data.group_id) === String(currentTarget)) {
          setCurrentTarget(null);
          setCurrentTargetId(null);
        }
      }));

      unsubs.push(window.Bridge.on('file_sent', function (data) {
        setMessages(function (prev) {
          return prev.concat([{
            type: 'system',
            content: '[System] File sent: ' + (data.filename || 'unknown') + ' (' + (data.filesize || 0) + ' bytes)',
            timestamp: Math.floor(Date.now() / 1000),
            related_type: data.related_type || 'private',
            related_target: data.related_target || '',
          }]);
        });
      }));

      unsubs.push(window.Bridge.on('file_download_result', function (data) {
        setMessages(function (prev) {
          return prev.concat([{
            type: 'system',
            content: data.success
              ? '[System] File saved: ' + data.filename + ' (' + (data.filesize || 0) + ' bytes) -> ' + (data.path || 'downloads/')
              : '[System] File download failed: ' + (data.error || 'unknown error'),
            timestamp: Math.floor(Date.now() / 1000),
            related_type: data.related_type || 'private',
            related_target: data.related_target || '',
          }]);
        });
      }));

      unsubs.push(window.Bridge.on('file_incoming', function (data) {
        setMessages(function (prev) {
          return prev.concat([{
            type: 'system',
            content: '[System] Incoming file from ' + (data.sender || ('User#' + (data.from_id || '?'))) + ': ' + data.filename + ' (' + (data.filesize || 0) + ' bytes)',
            timestamp: Math.floor(Date.now() / 1000),
            related_type: data.related_type || 'private',
            related_target: data.related_target || String(data.from_id || ''),
          }]);
        });
      }));

      return function () {
        unsubs.forEach(function (fn) { fn(); });
      };
    }, [currentTarget]);

    // 选择聊天目标
    var handleSelectTarget = useCallback(function (type, name, id) {
      setCurrentTarget(name);
      setCurrentTargetId(id);
      setCurrentChatType(type);
      window.Bridge.setCurrentTarget(name, id, type);
      // 清除该目标的未读计数
      var key = type === 'group' ? 'group:' + name :
                type === 'ai' ? 'ai:' + name :
                'private:' + (name || id);
      setUnreadCounts(function (prev) {
        var next = Object.assign({}, prev);
        delete next[key];
        return next;
      });
      if (type === 'ai') {
        // AI 聊天：不请求服务器历史（用本地消息）
      } else if (type === 'private') {
        window.Bridge.requestHistory('private', id);
      } else {
        window.Bridge.requestHistory('group', parseInt(name));
      }
    }, []);

    // 发送消息（包含 AI 聊天路由）
    var handleSend = useCallback(function (content) {
      if (currentChatType === 'ai') {
        // 收集最近对话作为上下文
        var ctx = messages.filter(function (m) { return m.type === 'ai'; }).slice(-10).map(function (m) {
          return { sender: m.sender, content: m.content };
        });
        window.Bridge.sendAiQuery(content, 0, ctx);
        // 本地显示用户消息
        setMessages(function (prev) {
          return prev.concat([{
            type: 'ai',
            sender: username,
            content: content,
            timestamp: Math.floor(Date.now() / 1000)
          }]);
        });
      } else if (currentChatType === 'private' && currentTargetId) {
        window.Bridge.sendPrivateMsg(currentTargetId, content);
      } else if (currentChatType === 'group') {
        window.Bridge.sendGroupMsg(parseInt(currentTarget), content);
      }
    }, [currentChatType, currentTargetId, currentTarget, username, messages]);

    // AI 查询
    var handleAI = useCallback(function (query) {
      if (!query) {
        setShowAiDialog(true);
        return;
      }
      var gid = currentChatType === 'group' ? parseInt(currentTarget) : 0;
      window.Bridge.sendAiQuery(query, gid);
      // 显示发送中提示（带聊天上下文）
      setMessages(function (prev) {
        return prev.concat([{
          type: 'system',
          content: '[AI] Query sent: "' + query.substring(0, 40) + (query.length > 40 ? '...' : '') + '"',
          timestamp: Math.floor(Date.now() / 1000),
          related_type: currentChatType,
          related_target: currentChatType === 'group' ? currentTarget : String(currentTargetId),
        }]);
      });
    }, [currentChatType, currentTarget]);

    // 文件发送
    var handleFile = useCallback(function () {
      window.Bridge.selectAndSendFile().then(function (result) {
        if (result && !result.ok) {
          setMessages(function (prev) {
            return prev.concat([{
              type: 'system',
              content: '[System] File upload: ' + (result.error || 'failed'),
              timestamp: Math.floor(Date.now() / 1000)
            }]);
          });
        } else if (result && result.ok) {
          setMessages(function (prev) {
            return prev.concat([{
              type: 'system',
              content: '[System] Sending file: ' + result.filename + ' (' + result.filesize + ' bytes)',
              timestamp: Math.floor(Date.now() / 1000)
            }]);
          });
        }
      }).catch(function (err) {
        console.warn('File upload error:', err);
      });
    }, []);

    // 群组操作
    var handleGroupCreate = useCallback(function () {
      setGroupDialogType('create');
      setGroupDialogTitle('Create Group');
      setGroupDialogValue('');
      setShowGroupDialog(true);
    }, []);

    var handleGroupJoin = useCallback(function () {
      setGroupDialogType('join');
      setGroupDialogTitle('Join Group');
      setGroupDialogValue('');
      setShowGroupDialog(true);
    }, []);

    var handleGroupLeave = useCallback(function () {
      setGroupDialogType('leave');
      setGroupDialogTitle('Leave Group');
      setGroupDialogValue('');
      setShowGroupDialog(true);
    }, []);

    var handleGroupSubmit = useCallback(function () {
      if (groupDialogType === 'create') {
        window.Bridge.groupCreate(groupDialogValue);
      } else if (groupDialogType === 'join') {
        window.Bridge.groupJoin(parseInt(groupDialogValue));
      } else if (groupDialogType === 'leave') {
        window.Bridge.groupLeave(parseInt(groupDialogValue));
      }
      setShowGroupDialog(false);
    }, [groupDialogType, groupDialogValue]);

    // 撤回消息
    var handleRecall = useCallback(function (msgId) {
      window.Bridge.sendRecall(msgId);
    }, []);

    // 关闭右键菜单
    useEffect(function () {
      var handler = function () { setContextMenu(null); };
      window.addEventListener('click', handler);
      return function () { window.removeEventListener('click', handler); };
    }, []);

    return h('div', { className: 'chat-layout' },
      // 侧边栏（附带群组管理按钮）
      h('div', { className: 'sidebar', ref: sidebarRef },
        // 用户信息头
        h('div', { className: 'sidebar-header' },
          h('div', { className: 'avatar', style: { background: getAvatarColor(username || 'Me') } },
            getInitials(username),
            h('div', { className: 'status-dot online' }),
          ),
          h('div', { className: 'user-info' },
            h('div', { className: 'username' }, username || 'Loading...'),
            h('div', { className: 'status-text' }, 'Online'),
          ),
        ),
        // 群组管理工具栏
        h('div', { className: 'sidebar-toolbar' },
          h('button', { className: 'toolbar-btn', onClick: handleGroupCreate, title: 'Create Group' }, '+ New Group'),
          h('button', { className: 'toolbar-btn', onClick: handleGroupJoin, title: 'Join Group' }, '+ Join'),
          h('button', { className: 'toolbar-btn', onClick: handleGroupLeave, title: 'Leave Group' }, '- Leave'),
        ),
        // 搜索框
        h('div', { className: 'sidebar-search' },
          h('input', {
            type: 'text',
            placeholder: 'Search contacts...',
            value: searchQuery,
            onChange: function (e) { setSearchQuery(e.target.value); },
          }),
        ),
        // 联系人列表 (Sidebar component)
        h(window.App.Sidebar, {
          username: username,
          onlineUsers: onlineUsers,
          groups: groups,
          currentTarget: currentTarget,
          currentChatType: currentChatType,
          onSelectTarget: handleSelectTarget,
          searchQuery: searchQuery,
          unreadCounts: unreadCounts,
        }),
      ),

      // 聊天主区域
      h('div', {
        className: 'chat-main',
        ref: chatMainRef,
      },
        !currentTarget
          ? h('div', {
              className: 'no-chat-selected',
              ref: welcomeRef,
              key: 'welcome-' + (username || 'guest'),
            },
              h('div', { className: 'icon' }, '💬'),
              h('h2', null, 'Welcome, ' + (username || '') + '!'),
              h('p', null, 'Select a contact or group to start chatting'),
            )
          : h(React.Fragment, null,
              h(ChatHeader, {
                targetName: currentTarget,
                chatType: currentChatType,
                onlineUsers: onlineUsers,
                headerRef: function(el) { headerRef.current = el; },
              }),
              h(window.App.MessageArea, {
                messages: filteredMessages,
                username: username,
                targetId: currentTargetId,
                chatType: currentChatType,
              }),
              h('div', { ref: inputRef },
                h(ChatInput, {
                  onSend: handleSend,
                  onAI: handleAI,
                  onFile: handleFile,
                  disabled: !currentTarget,
                }),
              ),
            ),
      ),

      // 状态栏
      h(StatusBar, {
        connected: connected,
        onlineCount: Object.keys(onlineUsers).length,
        groupCount: Object.keys(groups).length,
      }),

      // 对话框
      h(AIDialog, {
        visible: showAiDialog,
        onClose: function () { setShowAiDialog(false); },
        onSubmit: handleAI,
      }),
      h(GroupDialog, {
        visible: showGroupDialog,
        title: groupDialogTitle,
        dialogType: groupDialogType,
        value: groupDialogValue,
        onChange: setGroupDialogValue,
        onSubmit: handleGroupSubmit,
        onClose: function () { setShowGroupDialog(false); },
      }),
    );
  }

  window.App = window.App || {};
  window.App.ChatLayout = ChatLayout;
})();
