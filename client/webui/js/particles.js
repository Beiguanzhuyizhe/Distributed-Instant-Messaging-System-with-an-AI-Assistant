/**
 * particles.js — 登录页粒子背景动画
 * 灵感来源于 React Bits Strands 组件：流动光效 + 粒子系统
 */

(function () {
  'use strict';

  function initParticles(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    var width, height;
    var particles = [];
    var connections = [];
    var mouseX = -1000, mouseY = -1000;
    var animFrame;
    var running = true;

    var COLORS = ['#6c63ff', '#8b5cf6', '#a78bfa', '#7c3aed', '#6366f1'];

    function resize() {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    }

    function createParticle() {
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        radius: Math.random() * 2.5 + 1,
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
        alpha: Math.random() * 0.5 + 0.1,
      };
    }

    function init() {
      resize();
      var count = Math.min(80, Math.floor(width * height / 12000));
      particles = [];
      for (var i = 0; i < count; i++) {
        particles.push(createParticle());
      }
    }

    function draw() {
      if (!running) return;
      ctx.clearRect(0, 0, width, height);

      // Draw gradient background orbs
      var grd = ctx.createRadialGradient(width * 0.3, height * 0.4, 0, width * 0.3, height * 0.4, width * 0.5);
      grd.addColorStop(0, 'rgba(108, 99, 255, 0.04)');
      grd.addColorStop(1, 'rgba(13, 14, 26, 0)');
      ctx.fillStyle = grd;
      ctx.fillRect(0, 0, width, height);

      var grd2 = ctx.createRadialGradient(width * 0.7, height * 0.7, 0, width * 0.7, height * 0.7, width * 0.4);
      grd2.addColorStop(0, 'rgba(139, 92, 246, 0.03)');
      grd2.addColorStop(1, 'rgba(13, 14, 26, 0)');
      ctx.fillStyle = grd2;
      ctx.fillRect(0, 0, width, height);

      // Update & draw particles
      for (var i = 0; i < particles.length; i++) {
        var p = particles[i];
        p.x += p.vx;
        p.y += p.vy;

        // Mouse interaction (gentle attraction/repulsion)
        var dx = mouseX - p.x;
        var dy = mouseY - p.y;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 200 && dist > 0) {
          var force = (200 - dist) / 200 * 0.02;
          p.vx -= dx / dist * force;
          p.vy -= dy / dist * force;
        }

        // Boundary wrap
        if (p.x < -10) p.x = width + 10;
        if (p.x > width + 10) p.x = -10;
        if (p.y < -10) p.y = height + 10;
        if (p.y > height + 10) p.y = -10;

        // Damping
        p.vx *= 0.999;
        p.vy *= 0.999;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = p.alpha;
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      // Draw connections
      for (var i = 0; i < particles.length; i++) {
        for (var j = i + 1; j < particles.length; j++) {
          var a = particles[i], b = particles[j];
          var dx = a.x - b.x;
          var dy = a.y - b.y;
          var dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 150) {
            var alpha = (1 - dist / 150) * 0.12;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.strokeStyle = 'rgba(108, 99, 255, ' + alpha + ')';
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }

      animFrame = requestAnimationFrame(draw);
    }

    // Mouse tracking
    function onMouseMove(e) {
      mouseX = e.clientX;
      mouseY = e.clientY;
    }

    function onResize() {
      resize();
    }

    init();
    draw();

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('resize', onResize);

    return {
      destroy: function () {
        running = false;
        cancelAnimationFrame(animFrame);
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('resize', onResize);
      },
      resize: function () {
        resize();
      }
    };
  }

  window.initParticles = initParticles;
})();
