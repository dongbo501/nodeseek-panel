import * as THREE from 'three';

const host = document.getElementById('scene-layer');
const canvas = document.getElementById('orbit-canvas');
const stage = document.getElementById('orbital-stage');
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
let renderer;

function createOrbit() {
  renderer = new THREE.WebGLRenderer({canvas, alpha: true, antialias: true, powerPreference: 'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.4;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, .1, 40);
  camera.position.set(0, .1, 9.8);
  const ambient = new THREE.HemisphereLight(0xf8ffff, 0x6b746d, 3.4);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 5); key.position.set(-3, 6, 5); scene.add(key);
  const fill = new THREE.DirectionalLight(0xa9eeeb, 3.8); fill.position.set(5, 1, 2); scene.add(fill);
  const edge = new THREE.DirectionalLight(0xff9373, 3.2); edge.position.set(-3, -2, -2); scene.add(edge);
  const root = new THREE.Group(); root.rotation.set(.42, -.43, -.32); scene.add(root);
  const silver = new THREE.MeshPhysicalMaterial({color: 0xc7d1d0, metalness: .68, roughness: .24, clearcoat: 1});
  const graphite = new THREE.MeshStandardMaterial({color: 0x26302e, metalness: .35, roughness: .34});
  const coral = new THREE.MeshStandardMaterial({color: 0xf14b2d, metalness: .18, roughness: .28, emissive: 0x591207, emissiveIntensity: .12});
  const mint = new THREE.MeshStandardMaterial({color: 0xa1e8dc, metalness: .1, roughness: .2, emissive: 0x1e534b, emissiveIntensity: .18});
  const trackMaterial = new THREE.MeshStandardMaterial({color: 0x697a74, metalness: .65, roughness: .3});
  const materials = [silver, graphite, coral, mint, trackMaterial];
  const mesh = (geometry, material, parent = root) => { const value = new THREE.Mesh(geometry, material); parent.add(value); return value; };
  mesh(new THREE.TorusGeometry(1.7, .19, 24, 110), silver);
  const outerTrack = mesh(new THREE.TorusGeometry(2.08, .024, 8, 100), trackMaterial);
  outerTrack.rotation.set(.12, -.08, 0);
  const orbit = new THREE.Group(); root.add(orbit);
  const innerRing = mesh(new THREE.TorusGeometry(1.18, .1, 16, 90), graphite, orbit);
  innerRing.rotation.x = .65;
  const sweep = mesh(new THREE.TorusGeometry(1.44, .055, 12, 80, Math.PI * 1.1), coral, orbit);
  sweep.rotation.set(-.45, .3, -.6);
  const secondSweep = mesh(new THREE.TorusGeometry(2.27, .035, 10, 70, Math.PI * .7), coral);
  secondSweep.rotation.set(.1, -.2, .6);
  const counterRing = mesh(new THREE.TorusGeometry(.79, .035, 10, 80), mint, orbit);
  counterRing.rotation.y = -.6;
  const insignia = new THREE.Group(); root.add(insignia);
  const left = mesh(new THREE.BoxGeometry(.21, .85, .22), coral, insignia); left.position.x = -.28;
  const right = mesh(new THREE.BoxGeometry(.21, .85, .22), coral, insignia); right.position.x = .28;
  const diagonal = mesh(new THREE.BoxGeometry(.19, 1, .22), coral, insignia); diagonal.rotation.z = .57;
  insignia.position.z = .14;
  const ticks = [];
  for (let i = 0; i < 56; i++) {
    const angle = i / 56 * Math.PI * 2;
    const tick = mesh(new THREE.BoxGeometry(i % 4 === 0 ? .045 : .025, i % 4 === 0 ? .15 : .07, .04), graphite);
    tick.position.set(Math.cos(angle) * 1.97, Math.sin(angle) * 1.97, .02);
    tick.rotation.z = angle - Math.PI / 2;
  }
  for (let i = 0; i < 14; i++) {
    const angle = i / 14 * Math.PI * 2;
    const tick = mesh(new THREE.BoxGeometry(.11, .21, .11), mint);
    tick.position.set(Math.cos(angle) * 2.09, Math.sin(angle) * 2.09, .1);
    tick.rotation.z = angle - Math.PI / 2; ticks.push(tick);
  }
  const satellite = mesh(new THREE.OctahedronGeometry(.14, 0), coral, orbit);
  satellite.position.set(2.27, 0, 0);
  let visible = false, frame = 0, elapsed = 0, previous = 0, disposed = false;
  let targetX = 0, targetY = 0, tiltX = 0, tiltY = 0, phase = 'none';
  const isMoving = () => !reduceMotion.matches && document.body.dataset.motion === 'on';
  function draw(now = 0, once = false) {
    if (disposed) return;
    if (!once && now - previous < 32) return;
    const delta = Math.min((now - previous) / 1000 || 0, .05); previous = now;
    if (!once && isMoving()) elapsed += delta;
    tiltX += (targetX - tiltX) * .07; tiltY += (targetY - tiltY) * .07;
    const speed = phase === 'running' ? .8 : .14;
    root.rotation.x = .42 + Math.sin(elapsed * .32) * .12 + tiltY;
    root.rotation.y = -.43 + Math.sin(elapsed * .23) * .2 + tiltX;
    root.rotation.z = -.32 + Math.sin(elapsed * .14) * .06;
    orbit.rotation.z = elapsed * speed;
    innerRing.rotation.y = Math.sin(elapsed * .22) * .4;
    secondSweep.rotation.z = .6 - elapsed * speed * .6;
    insignia.rotation.y = Math.sin(elapsed * .3) * .18;
    renderer.render(scene, camera);
    canvas.dataset.frame = String(++frame);
  }
  function syncLoop() {
    if (disposed) return;
    const active = visible && !document.hidden && !document.getElementById('overview').hidden && !document.getElementById('dashboard').hidden;
    if (active) draw(performance.now(), true);
    previous = performance.now();
    renderer.setAnimationLoop(active && isMoving() ? draw : null);
    canvas.dataset.motion = active && isMoving() ? 'running' : 'paused';
  }
  function resize() {
    const {width, height} = host.getBoundingClientRect();
    if (width < 1 || height < 1 || disposed) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.position.z = width < 400 ? 10.4 / Math.min(camera.aspect, 1) : 9.8;
    camera.updateProjectionMatrix();
    draw(performance.now(), true);
  }
  function applyState(detail) {
    if (!detail) return;
    phase = detail.phase || 'none';
    coral.color.set(phase === 'ok' ? 0xef4c2b : phase === 'fail' ? 0xd71f40 : phase === 'running' ? 0xf77719 : 0xf14b2d);
    (detail.days || []).forEach((status, index) => { if (ticks[index]) ticks[index].material = status === 'success' ? mint : status === 'fail' ? coral : graphite; });
    canvas.dataset.phase = phase;
    syncLoop();
  }
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(host);
  const intersection = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; syncLoop(); }, {threshold: .05}); intersection.observe(host);
  stage.addEventListener('pointermove', event => {
    if (!isMoving() || event.pointerType === 'touch') return;
    const rect = stage.getBoundingClientRect();
    targetX = ((event.clientX - rect.left) / rect.width - .5) * .32;
    targetY = ((event.clientY - rect.top) / rect.height - .5) * .18;
  });
  stage.addEventListener('pointerleave', () => { targetX = 0; targetY = 0; });
  document.addEventListener('nodeseek:scene', event => applyState(event.detail));
  document.addEventListener('nodeseek:motion', syncLoop);
  document.addEventListener('nodeseek:view', () => { resize(); syncLoop(); });
  document.addEventListener('visibilitychange', syncLoop);
  reduceMotion.addEventListener('change', syncLoop);
  canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); renderer.setAnimationLoop(null); host.dataset.ready = 'false'; });
  canvas.addEventListener('webglcontextrestored', () => { host.dataset.ready = 'true'; resize(); syncLoop(); });
  window.addEventListener('pageshow', syncLoop);
  window.addEventListener('pagehide', event => {
    renderer.setAnimationLoop(null);
    if (event.persisted) return;
    disposed = true; resizeObserver.disconnect(); intersection.disconnect();
    scene.traverse(item => item.geometry?.dispose()); materials.forEach(material => material.dispose()); renderer.dispose();
  });
  applyState(window.nodeseekScene); resize();
  host.dataset.ready = 'true';
  document.dispatchEvent(new Event('nodeseek:scene-ready'));
}
try { createOrbit(); } catch {
  renderer?.dispose();
  host.dataset.ready = 'false';
  host.dataset.fallback = 'true';
}
