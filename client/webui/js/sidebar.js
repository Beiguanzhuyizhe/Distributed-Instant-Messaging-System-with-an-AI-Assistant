/**
 * sidebar.js — 联系人 & 群组列表组件（含未读红点）
 */

(function () {
  'use strict';

  var h = React.createElement;
  var useMemo = React.useMemo;

  // ---- 头像颜色 ----
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

  // ---- 联系人项（含未读红点） ----
  function ContactItem(props) {
    var name = props.name;
    var isActive = props.isActive;
    var isOnline = props.isOnline;
    var isGroup = props.isGroup;
    var onClick = props.onClick;
    var unreadCount = props.unreadCount || 0;

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
      unreadCount > 0 && h('div', { className: 'unread-badge' },
        unreadCount > 99 ? '99+' : String(unreadCount)
      ),
    );
  }

  // ---- 联系人列表 ----
  function Sidebar(props) {
    var onlineUsers = props.onlineUsers || {};
    var groups = props.groups || {};
    var currentTarget = props.currentTarget;
    var currentChatType = props.currentChatType;
    var onSelectTarget = props.onSelectTarget;
    var searchQuery = props.searchQuery || '';
    var unreadCounts = props.unreadCounts || {};

    // 过滤 + 排序联系人（有未读的排前面，在线次之，最后字母序）
    var filteredUsers = useMemo(function () {
      var entries = Object.entries(onlineUsers);
      if (searchQuery) {
        var q = searchQuery.toLowerCase();
        entries = entries.filter(function (e) { return e[0].toLowerCase().includes(q); });
      }
      entries.sort(function (a, b) {
        // 自己始终排在最前面
        if (a[0] === props.username) return -1;
        if (b[0] === props.username) return 1;
        var keyA = 'private:' + a[0];
        var keyB = 'private:' + b[0];
        var unreadA = unreadCounts[keyA] || 0;
        var unreadB = unreadCounts[keyB] || 0;
        if (unreadA !== unreadB) return unreadB - unreadA; // 未读多的在前
        // 在线用户已在 onlineUsers 中，所以都是在线
        return a[0].localeCompare(b[0]);
      });
      return entries;
    }, [onlineUsers, searchQuery, props.username, unreadCounts]);

    var filteredGroups = useMemo(function () {
      var entries = Object.entries(groups);
      if (searchQuery) {
        var q = searchQuery.toLowerCase();
        entries = entries.filter(function (e) { return e[1].toLowerCase().includes(q); });
      }
      entries.sort(function (a, b) {
        var keyA = 'group:' + a[0];
        var keyB = 'group:' + b[0];
        var unreadA = unreadCounts[keyA] || 0;
        var unreadB = unreadCounts[keyB] || 0;
        if (unreadA !== unreadB) return unreadB - unreadA;
        return a[1].localeCompare(b[1]);
      });
      return entries;
    }, [groups, searchQuery, unreadCounts]);

    return h('div', { className: 'contact-list' },
      // AI 助手（置顶）
      h('div', { className: 'contact-section-title' },
        h('span', null, 'AI'),
      ),
      h(ContactItem, {
        name: 'AI Assistant',
        isActive: currentTarget === 'AI Assistant' && currentChatType === 'ai',
        isOnline: true,
        unreadCount: 0,
        onClick: function () { onSelectTarget('ai', 'AI Assistant', -1); },
      }),

      // 在线用户分组
      h('div', { className: 'contact-section-title', style: { marginTop: '4px' } },
        h('span', null, 'Online'),
        h('span', { className: 'count' }, Object.keys(onlineUsers).length),
      ),
      filteredUsers.map(function (entry) {
        var name = entry[0];
        var uid = entry[1];
        var isActive = currentTarget === name && currentChatType === 'private';
        var key = 'private:' + name;
        return h(ContactItem, {
          key: 'user-' + uid,
          name: name,
          isActive: isActive,
          isOnline: true,
          unreadCount: unreadCounts[key] || 0,
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
        var key = 'group:' + gid;
        return h(ContactItem, {
          key: 'group-' + gid,
          name: gname + ' (' + gid + ')',
          isActive: isActive,
          isGroup: true,
          unreadCount: unreadCounts[key] || 0,
          onClick: function () { onSelectTarget('group', gid, parseInt(gid)); },
        });
      }),
    );
  }

  window.App = window.App || {};
  window.App.Sidebar = Sidebar;
})();
