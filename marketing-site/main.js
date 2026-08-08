(function () {
  function initSpine(canvasId) {
    var container = document.getElementById(canvasId);
    if (!container) return;

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
    camera.position.set(0.5, 0, 6.5);
    var renderer = new THREE.WebGLRenderer({ canvas: container, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    function resize() {
      var w = container.clientWidth || container.parentElement.clientWidth;
      var h = container.clientHeight || container.parentElement.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    }

    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    var key = new THREE.PointLight(0xffffff, 1.1, 20);
    key.position.set(3, 3, 4);
    scene.add(key);
    var fill = new THREE.PointLight(0x8fb3ff, 0.6, 20);
    fill.position.set(-3, -2, 3);
    scene.add(fill);

    var spineGroup = new THREE.Group();
    var segCount = 14;
    var vertMat = new THREE.MeshStandardMaterial({ color: 0x1a3d7c, metalness: 0.3, roughness: 0.4 });
    var discMat = new THREE.MeshStandardMaterial({ color: 0xd8e0f2, metalness: 0.1, roughness: 0.6 });

    for (var i = 0; i < segCount; i++) {
      var y = 1.9 - i * 0.28;
      var curveX = Math.sin(i * 0.35) * 0.35;
      var vertGeo = new THREE.SphereGeometry(0.15, 12, 10);
      vertGeo.scale(1, 0.55, 0.85);
      var vert = new THREE.Mesh(vertGeo, vertMat);
      vert.position.set(curveX, y, 0);
      spineGroup.add(vert);
      if (i < segCount - 1) {
        var disc = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.05, 16), discMat);
        disc.position.set(curveX, y - 0.14, 0);
        spineGroup.add(disc);
      }
    }

    var glow = new THREE.Mesh(
      new THREE.TorusGeometry(1.6, 0.012, 8, 64),
      new THREE.MeshBasicMaterial({ color: 0x8fb3ff, transparent: true, opacity: 0.35 })
    );
    glow.rotation.x = Math.PI / 2.2;
    spineGroup.add(glow);

    var particles = new THREE.Group();
    var pMat = new THREE.MeshStandardMaterial({ color: 0xbcd2f5, roughness: 0.3, metalness: 0.4 });
    for (var j = 0; j < 6; j++) {
      var s = new THREE.Mesh(new THREE.SphereGeometry(0.035 + Math.random() * 0.03, 10, 10), pMat);
      var ang = Math.random() * Math.PI * 2;
      var rad = 1.3 + Math.random() * 0.5;
      s.position.set(Math.cos(ang) * rad, (Math.random() - 0.5) * 3, Math.sin(ang) * rad * 0.4);
      s.userData.baseY = s.position.y;
      s.userData.phase = Math.random() * Math.PI * 2;
      particles.add(s);
    }
    spineGroup.add(particles);
    scene.add(spineGroup);

    var clock = new THREE.Clock();
    var running = true;
    var frameId = null;

    function tick() {
      if (!running) return;
      frameId = requestAnimationFrame(tick);
      var t = clock.getElapsedTime();
      spineGroup.rotation.y = Math.sin(t * 0.3) * 0.5;
      glow.rotation.z = t * 0.15;
      particles.children.forEach(function (s) {
        s.position.y = s.userData.baseY + Math.sin(t * 0.6 + s.userData.phase) * 0.15;
      });
      renderer.render(scene, camera);
    }

    resize();
    tick();
    window.addEventListener('resize', resize);

    if ('IntersectionObserver' in window) {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting && !running) {
            running = true;
            tick();
          } else if (!entry.isIntersecting && running) {
            running = false;
            if (frameId) cancelAnimationFrame(frameId);
          }
        });
      }, { threshold: 0.05 });
      observer.observe(container);
    }
  }

  initSpine('spine3d');
})();

(function () {
  var form = document.getElementById('booking-form');
  if (!form) return;

  var submitBtn = form.querySelector('.booking-submit');
  var submitLabel = form.querySelector('.booking-submit-label');
  var status = form.querySelector('.booking-status');

  form.addEventListener('submit', function (event) {
    event.preventDefault();

    var data = {
      name: form.name.value.trim(),
      phone: form.phone.value.trim(),
      email: form.email.value.trim(),
      message: form.message.value.trim(),
      website: form.website.value // honeypot, left as-is
    };

    if (!data.name) {
      status.textContent = 'Please tell us your name.';
      status.className = 'booking-status error';
      return;
    }
    if (!data.phone && !data.email) {
      status.textContent = 'Please share a phone number or email so we can reach you.';
      status.className = 'booking-status error';
      return;
    }

    submitBtn.disabled = true;
    submitLabel.textContent = 'Sending...';
    status.textContent = '';
    status.className = 'booking-status';

    fetch('/api/lead', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (result.ok) {
          form.reset();
          status.textContent = "Thanks! We've received your details and will call you back shortly.";
          status.className = 'booking-status ok';
        } else {
          status.textContent = (result.body && result.body.error) || 'Something went wrong — please WhatsApp or call us instead.';
          status.className = 'booking-status error';
        }
      })
      .catch(function () {
        status.textContent = 'Something went wrong — please WhatsApp or call us instead.';
        status.className = 'booking-status error';
      })
      .finally(function () {
        submitBtn.disabled = false;
        submitLabel.textContent = 'Request a call back';
      });
  });
})();
