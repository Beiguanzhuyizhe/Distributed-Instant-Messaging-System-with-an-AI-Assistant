const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const chatJs = fs.readFileSync(
  path.join(__dirname, '..', 'client', 'webui', 'js', 'chat.js'),
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

const routing = sandbox.window.App.__chatRouting;
assert(routing, 'chat routing test hooks should be exposed');

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
