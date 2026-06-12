/**
 * sidebar.jsx — 联系人 & 群组侧边栏组件
 */

(function () {
  'use strict';

  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;

  // ---- 头像颜色生成 ----
  var AVATAR_COLORS = [
    '#6c63ff', '#e91e63', '#ff9800', '#4caf50', '#2196f3',
    '#9c27b0', '#f44336', '#00bcd4', '#ff5722', '#3f51b5',
  ];

  function getAvatarColor(name) {
    var hash = 0;
    for (var i = 0; i < name.length; i++) {
      hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
  }

  function getInitials(name) {
    if (!name) return '?';
    return name.charAt(0).toUpperCase();
  }

  // ---- 联系人项 ----
  function ContactItem(props) {
    var name = props.name;
    var isActive = props.isActive;
    var isOnline = props.isOnline;
    var isGroup = props.isGroup;
    var onClick = props.onClick;

    var avatarColor = useMemo(function () { return getAvatarColor(name); }, [name]);

    return h('div', {
      className: 'contact-item' + (isActive ? ' active' : ''),
      onClick: onClick,
      title: name,
    },
      isGroup
        ? h('div', { className: 'group-icon' }, '#')
        : h('div', { className: 'contact-avatar', style: { background: avatarColor } },
            getInitials(name),
            isOnline
              ? h('div', { className: 'online-dot' })
              : h('div', { className: 'offline-dot' }),
          ),
      h('div', { className: 'contact-name' }, name),
    );
  }

  // ---- 侧边栏 ----
  function Sidebar(props) {
    var username = props.username;
    var onlineUsers = props.onlineUsers || {};
    var groups = props.groups || {};
    var currentTarget = props.currentTarget;
    var currentChatType = props.currentChatType;
    var onSelectTarget = props.onSelectTarget;

    var _useState = useState(''), searchQuery = _useState[0], setSearchQuery = _useState[1];

    // 过滤联系人
    var filteredUsers = useMemo(function () {
      var entries = Object.entries(onlineUsers);
      if (searchQuery) {
        var q = searchQuery.toLowerCase();
        entries = entries.filter(function (e) { return e[0].toLowerCase().includes(q); });
      }
      // 在线用户：自己排最前，其他按字母序
      entries.sort(function (a, b) {
        if (a[0] === username) return -1;
        if (b[0] === username) return 1;
        return a[0].localeCompare(b[0]);
      });
      return entries;
    }, [onlineUsers, searchQuery, username]);

    var filteredGroups = useMemo(function () {
      var entries = Object.entries(groups);
      if (searchQuery) {
        var q = searchQuery.toLowerCase();
        entries = entries.filter(function (e) { return e[1].toLowerCase().includes(q); });
      }
      return entries;
    }, [groups, searchQuery]);

    var selfColor = useMemo(function () { return getAvatarColor(username || 'Me'); }, [username]);

    return h('div', { className: 'sidebar' },
      // 用户信息头
      h('div', { className: 'sidebar-header' },
        h('div', { className: 'avatar', style: { background: selfColor } },
          getInitials(username),
          h('div', { className: 'status-dot online' }),
        ),
        h('div', { className: 'user-info' },
          h('div', { className: 'username' }, username || 'Loading...'),
          h('div', { className: 'status-text' }, 'Online'),
        ),
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

      // 联系人列表
      h('div', { className: 'contact-list' },
        // 在线用户分组
        h('div', { className: 'contact-section-title' },
          h('span', null, 'Online'),
          h('span', { className: 'count' }, Object.keys(onlineUsers).length),
        ),
        filteredUsers.map(function (entry) {
          var name = entry[0];
          var uid = entry[1];
          var isActive = currentTarget === name && currentChatType === 'private';
          return h(ContactItem, {
            key: 'user-' + uid,
            name: name,
            isActive: isActive,
            isOnline: true,
            onClick: function () { onSelectTarget('private', name, uid); },
          });
        }),

        // 群组分隔
        Object.keys(groups).length > 0 && h('div', { className: 'contact-section-title', style: { marginTop: '12px' } },
          h('span', null, 'Groups'),
          h('span', { className: 'count' }, Object.keys(groups).length),
        ),
        filteredGroups.map(function (entry) {
          var gid = entry[0];
          var gname = entry[1];
          var isActive = currentTarget === gid && currentChatType === 'group';
          return h(ContactItem, {
            key: 'group-' + gid,
            name: gname + ' (' + gid + ')',
            isActive: isActive,
            isGroup: true,
            onClick: function () { onSelectTarget('group', gid, parseInt(gid)); },
          });
        }),
      ),
    );
  }

  window.App = window.App || {};
  window.App.Sidebar = Sidebar;
})();
