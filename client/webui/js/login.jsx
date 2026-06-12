/**
 * login.jsx — 登录 / 注册页面组件
 */

(function () {
  'use strict';

  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;

  function LoginPage(props) {
    var onLogin = props.onLogin;
    var serverHost = props.serverHost || '127.0.0.1';
    var serverPort = props.serverPort || '8888';

    var _u = useState(''),          username    = _u[0], setUsername    = _u[1];
    var _p = useState(''),          password    = _p[0], setPassword    = _p[1];
    var _m = useState('login'),     mode        = _m[0], setMode        = _m[1];
    var _e = useState(null),        error       = _e[0], setError       = _e[1];
    var _s = useState(null),        success     = _s[0], setSuccess     = _s[1];
    var _l = useState(false),       loading     = _l[0], setLoading     = _l[1];
    var _v = useState(false),       cardVisible = _v[0], setCardVisible = _v[1];

    var cardRef = useRef(null);
    var particlesRef = useRef(null);
    var usernameRef = useRef(null);
    var onLoginRef = useRef(onLogin);
    onLoginRef.current = onLogin;

    // 入场动画
    useEffect(function () {
      requestAnimationFrame(function () {
        setCardVisible(true);
      });
      // 初始化粒子背景
      var p = window.initParticles('particles-canvas');
      particlesRef.current = p;
      return function () {
        if (p) p.destroy();
      };
    }, []);

    // 自动聚焦
    useEffect(function () {
      if (usernameRef.current) {
        usernameRef.current.focus();
      }
    }, [mode]);

    // 监听 Python 事件
    useEffect(function () {
      var unsub1 = window.Bridge.on('login_success', function (data) {
        setLoading(false);
        if (onLoginRef.current) onLoginRef.current(data);
      });
      var unsub2 = window.Bridge.on('login_error', function (data) {
        setLoading(false);
        setError(data.error);
        setSuccess(null);
      });
      var unsub3 = window.Bridge.on('register_success', function () {
        setLoading(false);
        setSuccess('Registration successful! Please login.');
        setError(null);
        setMode('login');
      });
      var unsub4 = window.Bridge.on('register_error', function (data) {
        setLoading(false);
        setError(data.error);
        setSuccess(null);
      });
      return function () {
        unsub1(); unsub2(); unsub3(); unsub4();
      };
    }, []);

    function handleSubmit(e) {
      e.preventDefault();
      if (!username.trim() || !password.trim()) {
        setError('Please enter both username and password');
        return;
      }
      setError(null);
      setSuccess(null);
      setLoading(true);
      if (mode === 'login') {
        window.Bridge.login(username.trim(), password);
      } else {
        window.Bridge.register(username.trim(), password);
      }
    }

    function switchMode() {
      setMode(mode === 'login' ? 'register' : 'login');
      setError(null);
      setSuccess(null);
    }

    return h('div', { className: 'login-page' },
      h('canvas', { id: 'particles-canvas' }),
      h('div', {
        className: 'login-card' + (cardVisible ? ' visible' : ''),
        ref: cardRef,
      },
        h('div', { className: 'logo' },
          h('div', { className: 'logo-icon' }, '\u{1F4AC}'),
          h('h1', null, 'Chat System'),
          h('p', null, 'Distributed Instant Messaging'),
        ),

        error && h('div', { className: 'error-message visible' }, error),
        success && h('div', { className: 'success-message visible' }, success),

        h('form', { onSubmit: handleSubmit },
          h('div', { className: 'form-group' },
            h('label', { htmlFor: 'username' }, 'Username'),
            h('input', {
              ref: usernameRef,
              id: 'username', type: 'text',
              placeholder: 'Enter your username',
              value: username,
              onChange: function (e) { setUsername(e.target.value); },
              disabled: loading,
            }),
          ),
          h('div', { className: 'form-group' },
            h('label', { htmlFor: 'password' }, 'Password'),
            h('input', {
              id: 'password', type: 'password',
              placeholder: 'Enter your password',
              value: password,
              onChange: function (e) { setPassword(e.target.value); },
              disabled: loading,
            }),
          ),
          h('div', { className: 'button-group' },
            h('button', {
              type: 'submit',
              className: 'btn btn-primary' + (loading ? ' loading' : ''),
              disabled: loading,
            },
              loading && h('span', { className: 'spinner' }),
              h('span', { className: 'btn-text' }, mode === 'login' ? 'Login' : 'Register'),
            ),
            h('button', {
              type: 'button',
              className: 'btn btn-secondary',
              onClick: switchMode,
              disabled: loading,
            }, mode === 'login' ? 'Register' : 'Back to Login'),
          ),
        ),
        h('div', { className: 'server-info' },
          'Server: ' + serverHost + ':' + serverPort
        ),
      ),
    );
  }

  window.App = window.App || {};
  window.App.LoginPage = LoginPage;
})();
