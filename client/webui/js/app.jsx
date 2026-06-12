/**
 * app.jsx — 应用根组件（路由：Login ↔ Chat）
 */

(function () {
  'use strict';

  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;

  function App() {
    var _useState = useState('login'), view = _useState[0], setView = _useState[1];
    var _useState2 = useState(null), userData = _useState2[0], setUserData = _useState2[1];
    var _useState3 = useState('127.0.0.1'), serverHost = _useState3[0], setServerHost = _useState3[1];
    var _useState4 = useState('8888'), serverPort = _useState4[0], setServerPort = _useState4[1];
    var loadedRef = React.useRef(false);

    // 应用启动时获取初始状态
    useEffect(function () {
      if (loadedRef.current) return;
      loadedRef.current = true;

      window.Bridge.ready().then(function () {
        // 获取连接信息
        window.Bridge.getConnectionStatus().then(function (status) {
          if (status && status.host) {
            setServerHost(status.host);
            setServerPort(String(status.port));
          }
        }).catch(function () {});
      });
    }, []);

    // 登录成功回调
    function handleLogin(data) {
      setUserData(data);
      // 使用 GSAP 做个过渡效果
      if (typeof gsap !== 'undefined') {
        var appEl = document.getElementById('app');
        if (appEl) {
          gsap.to(appEl, {
            opacity: 0,
            duration: 0.2,
            onComplete: function () {
              setView('chat');
              gsap.set(appEl, { opacity: 1 });
            }
          });
          return;
        }
      }
      setView('chat');
    }

    if (view === 'login') {
      return h(window.App.LoginPage, {
        onLogin: handleLogin,
        serverHost: serverHost,
        serverPort: serverPort,
      });
    }

    if (view === 'chat') {
      return h(window.App.ChatLayout, {
        username: userData ? userData.username : 'User',
      });
    }

    return null;
  }

  // 挂载 React 应用
  document.addEventListener('DOMContentLoaded', function () {
    var root = ReactDOM.createRoot(document.getElementById('app'));
    root.render(h(App));
  });
})();
