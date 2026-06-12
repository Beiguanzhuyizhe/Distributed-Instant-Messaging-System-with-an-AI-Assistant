/**
 * message-area.jsx — 消息列表 & 消息气泡组件
 */

(function () {
  'use strict';

  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;
  var useCallback = React.useCallback;
  var useMemo = React.useMemo;

  // ============================================================
  // 工具函数
  // ============================================================

  function formatTime(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var now = new Date();
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    var hh = pad(d.getHours());
    var mm = pad(d.getMinutes());

    // 今天：只显示时间
    if (d.toDateString() === now.toDateString()) {
      return hh + ':' + mm;
    }
    // 昨天
    var yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) {
      return 'Yesterday ' + hh + ':' + mm;
    }
    // 今年
    if (d.getFullYear() === now.getFullYear()) {
      return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hh + ':' + mm;
    }
    return d.getFullYear() + '/' + (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hh + ':' + mm;
  }

  function formatDateSeparator(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var now = new Date();
    if (d.toDateString() === now.toDateString()) return 'Today';
    var yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
    return d.getFullYear() + '/' + (d.getMonth() + 1) + '/' + d.getDate();
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  // ============================================================
  // 日期分隔线
  // ============================================================

  function DateSeparator(props) {
    return h('div', { className: 'date-separator' },
      h('span', null, props.label),
    );
  }

  // ============================================================
  // 消息气泡
  // ============================================================

  function MessageBubble(props) {
    var msg = props.message;
    var username = props.username;
    var prevMsg = props.prevMessage;
    var isSelf = msg.sender === username;
    var isSystem = msg.type === 'system';
    var isGroup = msg.type === 'group';

    // 判断是否与上一条消息连续（同一发送者，时间间隔 < 5 分钟）
    var isSameSender = false;
    if (prevMsg && !isSystem) {
      isSameSender = (prevMsg.sender === msg.sender || (isSelf && prevMsg.sender === username));
      if (prevMsg.type === 'system') isSameSender = false;
    }

    // 日期变化检测
    var showDateSeparator = false;
    if (prevMsg && msg.timestamp && prevMsg.timestamp) {
      var prevDate = new Date(prevMsg.timestamp * 1000).toDateString();
      var currDate = new Date(msg.timestamp * 1000).toDateString();
      showDateSeparator = prevDate !== currDate;
    }

    var isRecalled = msg.is_recalled || msg.content === '[已撤回]';

    return h(React.Fragment, null,
      showDateSeparator && h(DateSeparator, { label: formatDateSeparator(msg.timestamp) }),
      h('div', {
        className: 'message-wrapper' +
          (isSystem ? ' system' : isSelf ? ' self' : ' other') +
          (isSameSender ? ' same-sender' : ''),
      },
        // 群聊中显示发送者名称
        isGroup && !isSelf && !isSameSender && !isSystem &&
          h('div', { className: 'message-sender' }, msg.sender),

        // 消息气泡主体
        h('div', {
          className: 'message-bubble' + (isRecalled ? ' recalled' : ''),
          title: msg.timestamp ? formatTime(msg.timestamp) : '',
        },
          isRecalled
            ? '[Message recalled]'
            : msg.content
        ),

        // 时间戳
        !isSystem && h('div', { className: 'message-time' }, formatTime(msg.timestamp)),
      ),
    );
  }

  // ============================================================
  // 消息列表
  // ============================================================

  function MessageArea(props) {
    var messages = props.messages || [];
    var username = props.username;
    var areaRef = useRef(null);
    var bottomRef = useRef(null);
    var _useState = useState(false), atBottom = _useState[0], setAtBottom = _useState[1];
    var autoScrollRef = useRef(true);

    // 自动滚动到底部
    useEffect(function () {
      if (autoScrollRef.current && bottomRef.current) {
        bottomRef.current.scrollIntoView({ behavior: 'smooth' });
      }
    }, [messages]);

    // 检测滚动位置
    var handleScroll = useCallback(function () {
      var el = areaRef.current;
      if (!el) return;
      var threshold = 100;
      var isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
      autoScrollRef.current = isNearBottom;
      setAtBottom(isNearBottom);
    }, []);

    // 新的消息列表到来时，自动滚到底部
    var scrollToBottom = useCallback(function () {
      if (bottomRef.current) {
        bottomRef.current.scrollIntoView({ behavior: 'smooth' });
        autoScrollRef.current = true;
        setAtBottom(true);
      }
    }, []);

    // 当前聊天变化时重置滚动
    useEffect(function () {
      autoScrollRef.current = true;
      setAtBottom(true);
    }, [props.targetId, props.chatType]);

    return h('div', { className: 'message-container' },
      h('div', {
        className: 'message-area',
        ref: areaRef,
        onScroll: handleScroll,
      },
        messages.length === 0 &&
          h('div', { style: { textAlign: 'center', color: 'var(--text-muted)', padding: '40px 0', fontSize: '13px' } },
            'No messages yet. Start a conversation!'
          ),
        messages.map(function (msg, idx) {
          var prev = idx > 0 ? messages[idx - 1] : null;
          return h(MessageBubble, {
            key: msg.msg_id || msg.local_msg_id || idx,
            message: msg,
            prevMessage: prev,
            username: username,
          });
        }),
        h('div', { ref: bottomRef }),
      ),

      // 滚动到底部按钮
      h('button', {
        className: 'scroll-bottom-btn' + (atBottom ? '' : ' visible'),
        onClick: scrollToBottom,
        title: 'Scroll to bottom',
      }, '↓'),
    );
  }

  window.App = window.App || {};
  window.App.MessageArea = MessageArea;
})();
