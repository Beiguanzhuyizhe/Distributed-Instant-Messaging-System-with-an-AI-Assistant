const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const chatJs = fs.readFileSync(
  path.join(__dirname, '..', 'client', 'webui', 'js', 'chat.js'),
  'utf8',
);
const sidebarJs = fs.readFileSync(
  path.join(__dirname, '..', 'client', 'webui', 'js', 'sidebar.js'),
  'utf8',
);

const noop = () => {};
const React = {
  createElement: () => null,
  Fragment: Symbol('Fragment'),
  useState: () => [null, noop],
  useEffect: noop,
  useRef: () => ({ current: null }),
  useCallback: (fn) => fn,
  useMemo: (fn) => fn(),
};

const sandbox = {
  React,
  window: { App: {} },
  console,
};

vm.createContext(sandbox);
vm.runInContext(chatJs, sandbox);
vm.runInContext(sidebarJs, sandbox);

const routing = sandbox.window.App.__chatRouting;
assert(routing, 'chat routing test hooks should be exposed');
const sidebarLogic = sandbox.window.App.__sidebarLogic;
assert(sidebarLogic, 'sidebar logic test hooks should be exposed');

const messages = [
  {
    type: 'private',
    sender: 'alice',
    from_id: 1,
    target_id: 2,
    related_type: 'private',
    related_target: '2',
    chat_key: 'private:2',
    content: 'alice to bob',
  },
  {
    type: 'private',
    sender: 'alice',
    from_id: 1,
    target_id: 3,
    related_type: 'private',
    related_target: '3',
    chat_key: 'private:3',
    content: 'alice to carol',
  },
  {
    type: 'private',
    sender: 'bob',
    from_id: 2,
    target_id: 2,
    related_type: 'private',
    related_target: '2',
    chat_key: 'private:2',
    content: 'bob to alice',
  },
];

const bobVisible = messages.filter((message) => routing.messageBelongsToChat(message, {
  chatType: 'private',
  targetName: 'bob',
  targetId: 2,
  username: 'alice',
}));
assert.deepStrictEqual(bobVisible.map((m) => m.content), ['alice to bob', 'bob to alice']);

const carolVisible = messages.filter((message) => routing.messageBelongsToChat(message, {
  chatType: 'private',
  targetName: 'carol',
  targetId: 3,
  username: 'alice',
}));
assert.deepStrictEqual(carolVisible.map((m) => m.content), ['alice to carol']);

assert.strictEqual(routing.chatKeyForMessage(messages[0], 'alice'), 'private:2');
assert.strictEqual(routing.chatKeyForMessage(messages[2], 'alice'), 'private:2');
assert.strictEqual(routing.chatKeyForMessage({
  type: 'private',
  sender: 'bob',
  from_id: 2,
  receiver_id: 1,
  content: 'legacy inbound',
}, 'alice'), 'private:2');

const ownChat = messages.filter((message) => routing.messageBelongsToChat(message, {
  chatType: 'private',
  targetName: 'alice',
  targetId: 1,
  username: 'alice',
}));
assert.deepStrictEqual(ownChat.map((m) => m.content), []);

const groupMessage = {
  type: 'group',
  sender: 'bob',
  from_id: 2,
  group_id: '9',
  target_id: '9',
  related_type: 'group',
  related_target: '9',
  chat_key: 'group:9',
  content: 'group only',
};
assert.strictEqual(routing.messageBelongsToChat(groupMessage, {
  chatType: 'group',
  targetName: '9',
  targetId: 9,
  username: 'alice',
}), true);
assert.strictEqual(routing.messageBelongsToChat(groupMessage, {
  chatType: 'private',
  targetName: 'bob',
  targetId: 2,
  username: 'alice',
}), false);
assert.strictEqual(routing.messageBelongsToChat(groupMessage, {
  chatType: 'private',
  targetName: 'alice',
  targetId: 1,
  username: 'alice',
}), false);

const globalSystem = {
  type: 'system',
  content: 'unscoped system message',
};
assert.strictEqual(routing.messageBelongsToChat(globalSystem, {
  chatType: 'private',
  targetName: 'bob',
  targetId: 2,
  username: 'alice',
}), false);

const scopedSystem = {
  type: 'system',
  content: 'scoped system message',
  related_type: 'private',
  related_target: '2',
  chat_key: 'private:2',
};
assert.strictEqual(routing.messageBelongsToChat(scopedSystem, {
  chatType: 'private',
  targetName: 'bob',
  targetId: 2,
  username: 'alice',
}), true);
assert.strictEqual(routing.messageBelongsToChat(scopedSystem, {
  chatType: 'private',
  targetName: 'carol',
  targetId: 3,
  username: 'alice',
}), false);

const groupAi = {
  type: 'ai',
  sender: 'AI Assistant',
  content: 'group ai',
  group_id: 9,
  related_type: 'group',
  related_target: '9',
  chat_key: 'group:9',
};
assert.strictEqual(routing.messageBelongsToChat(groupAi, {
  chatType: 'group',
  targetName: '9',
  targetId: 9,
  username: 'alice',
}), true);
assert.strictEqual(routing.messageBelongsToChat(groupAi, {
  chatType: 'ai',
  targetName: 'AI Assistant',
  targetId: -1,
  username: 'alice',
}), false);

const aiDirect = {
  type: 'ai',
  sender: 'AI Assistant',
  content: 'direct ai',
  related_type: 'ai',
  related_target: 'AI Assistant',
  chat_key: 'ai:AI Assistant',
};
assert.strictEqual(routing.messageBelongsToChat(aiDirect, {
  chatType: 'ai',
  targetName: 'AI Assistant',
  targetId: -1,
  username: 'alice',
}), true);
assert.strictEqual(routing.messageBelongsToChat(aiDirect, {
  chatType: 'private',
  targetName: 'bob',
  targetId: 2,
  username: 'alice',
}), false);

const pendingOwn = {
  type: 'private',
  sender: 'alice',
  from_id: 1,
  receiver_id: 2,
  target_id: 2,
  content: 'same message',
  timestamp: 1700000000,
  local_msg_id: 'local-1',
  msg_id: 'local-1',
  status: 'pending',
  chat_key: 'private:2',
};
const historyOwn = {
  type: 'private',
  sender: 'alice',
  from_id: 1,
  receiver_id: 2,
  target_id: '2',
  content: 'same message',
  timestamp: 1700000001,
  msg_id: 'server-1',
  chat_key: 'private:2',
};
const merged = routing.mergeMessages([pendingOwn], [historyOwn], 'alice');
assert.strictEqual(merged.length, 1);
assert.strictEqual(merged[0].msg_id, 'server-1');
assert.strictEqual(merged[0].server_msg_id, 'server-1');
assert.strictEqual(merged[0].local_msg_id, 'local-1');
assert.strictEqual(merged[0].status, 'sent');

const repeatedRealMessages = routing.mergeMessages([{
  type: 'private',
  sender: 'alice',
  content: 'repeat',
  timestamp: 1700000000,
  msg_id: 'server-a',
  chat_key: 'private:2',
}], [{
  type: 'private',
  sender: 'alice',
  content: 'repeat',
  timestamp: 1700000001,
  msg_id: 'server-b',
  chat_key: 'private:2',
}], 'alice');
assert.strictEqual(repeatedRealMessages.length, 2);

assert.strictEqual(sidebarLogic.isSelfUser('alice', 'alice'), true);
assert.strictEqual(sidebarLogic.isSelfUser('bob', 'alice'), false);
assert.strictEqual(sidebarLogic.formatGroupLabel('2', '1'), '1');
assert.strictEqual(sidebarLogic.formatGroupLabel('1', 'group'), 'group');
assert.notStrictEqual(sidebarLogic.formatGroupLabel('2', '1'), '1 (2)');
assert.notStrictEqual(sidebarLogic.formatGroupLabel('2', '1'), '#2  1');
assert.strictEqual(routing.formatGroupTitle('2', '1'), '1');
assert.strictEqual(routing.avatarNameForChat('group', '3', '22'), '22');
assert.strictEqual(routing.avatarNameForChat('group', '3', ''), 'Group #3');
assert.strictEqual(routing.avatarNameForChat('ai', 'DeepSeek', ''), 'AI');
assert.strictEqual(routing.connectionStatusText(true), 'Online');
assert.strictEqual(routing.connectionStatusText(false), 'Offline');
assert.strictEqual(routing.connectionStatusClass(true), 'status-dot online');
assert.strictEqual(routing.connectionStatusClass(false), 'status-dot offline');
assert.strictEqual(routing.connectionStatusTextClass(true), 'status-text online');
assert.strictEqual(routing.connectionStatusTextClass(false), 'status-text offline');
