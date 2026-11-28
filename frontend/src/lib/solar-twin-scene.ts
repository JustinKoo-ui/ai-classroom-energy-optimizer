/**
 * Three.js scene for the solar tracker digital twin. Loaded on demand by
 * SolarDigitalTwin so the 3D library is only downloaded on the Solar tracker page.
 *
 * World axes: +X = east, -Z = north, +Y = up. One unit is about 10 cm.
 * The frame, plates, electrical box, belt drive and charge controller are fixed.
 * The turntable rotates to the measured azimuth and the panel tilts on its front
 * hinge to the measured tilt; the linear actuator follows the panel.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { directionVector, FIXED_YAW_TRAVEL, type TwinState } from "./solar-twin";

export type TwinView = "perspective" | "top" | "front";

export interface TwinScene {
  update(state: TwinState): void;
  setView(view: TwinView): void;
  dispose(): void;
}

// Dimensions (units ≈ 10 cm), measured by eye from the prototype photos.
const FRAME = { width: 6, depth: 6, height: 2.2, bar: 0.18 };
const TOP_Y = FRAME.height + 0.08;
const TURNTABLE = { x: 0, z: -0.2, radius: 1.95 };
const PANEL = { width: 3.6, length: 2.5, hingeZ: -1.15, hingeY: 0.32 };
const NEUTRAL = { azimuthDeg: 180, tiltDeg: 30 };
const SUN_DISTANCE = 5.4;

const COLORS = {
  aluminium: 0xc9ced3,
  acrylicBlack: 0x0c1112,
  acrylicTan: 0xc8a57c,
  box: 0xeef0ee,
  conduit: 0x151718,
  cell: 0x1f3f93,
  panelBack: 0xe8ebe9,
  belt: 0x1b1c1d,
  controller: 0x1d2430,
  screen: 0x2f8fe0,
  sun: 0xffc53d,
  facing: 0x2f9e6b,
  noTravel: 0xc45050,
  driveOn: 0xf0b429,
};

/** Bearing (0 = north, clockwise) to a world direction on the ground plane. */
function bearingDir(bearingDeg: number) {
  const b = (bearingDeg * Math.PI) / 180;
  return new THREE.Vector3(Math.sin(b), 0, -Math.cos(b));
}

/** World direction for compass azimuth + zenith. */
function skyDir(azimuthDeg: number, zenithDeg: number) {
  const v = directionVector(azimuthDeg, zenithDeg);
  return new THREE.Vector3(v.east, v.up, -v.north);
}

function shortestTurn(from: number, to: number) {
  return ((to - from + 540) % 360) - 180;
}

function cellTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 900;
  canvas.height = 600;
  const g = canvas.getContext("2d");
  if (g) {
    g.fillStyle = "#e9edf2";
    g.fillRect(0, 0, canvas.width, canvas.height);
    const cols = 9;
    const rows = 6;
    const gap = 6;
    const cw = canvas.width / cols;
    const ch = canvas.height / rows;
    for (let c = 0; c < cols; c++) {
      for (let r = 0; r < rows; r++) {
        const x = c * cw + gap / 2;
        const y = r * ch + gap / 2;
        const grad = g.createLinearGradient(x, y, x + cw, y + ch);
        grad.addColorStop(0, "#2a4fa8");
        grad.addColorStop(1, "#16306f");
        g.fillStyle = grad;
        g.fillRect(x, y, cw - gap, ch - gap);
        g.strokeStyle = "rgba(210,220,240,.35)";
        g.lineWidth = 1;
        for (let k = 1; k < 3; k++) {
          g.beginPath();
          g.moveTo(x + ((cw - gap) * k) / 3, y);
          g.lineTo(x + ((cw - gap) * k) / 3, y + ch - gap);
          g.stroke();
        }
      }
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

function labelTexture(text: string, color: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 128;
  const g = canvas.getContext("2d");
  if (g) {
    g.font = "600 76px Inter, system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = color;
    g.fillText(text, 128, 66);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function labelSprite(texture: THREE.Texture, size = 0.9): THREE.Sprite {
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthWrite: false, transparent: true }));
  sprite.scale.set(size * 2, size, 1);
  return sprite;
}

export function createTwinScene(container: HTMLElement, options: { reducedMotion: boolean }): TwinScene {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.setAttribute("aria-hidden", "true");
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  // Soft studio reflections so aluminium reads as silver, not dark grey.
  const pmrem = new THREE.PMREMGenerator(renderer);
  const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environment = environment;
  scene.environmentIntensity = 0.55;
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = !options.reducedMotion;
  controls.dampingFactor = 0.08;
  controls.minDistance = 6;
  controls.maxDistance = 30;
  controls.maxPolarAngle = Math.PI * 0.495;
  controls.target.set(0, 2, 0);

  const mat = (color: number, extra: THREE.MeshStandardMaterialParameters = {}) =>
    new THREE.MeshStandardMaterial({ color, ...extra });
  const aluminium = mat(COLORS.aluminium, { metalness: 0.85, roughness: 0.28, envMapIntensity: 1.6 });
  const add = <T extends THREE.Object3D>(parent: THREE.Object3D, object: T, cast = true, receive = true): T => {
    object.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        child.castShadow = cast;
        child.receiveShadow = receive;
      }
    });
    parent.add(object);
    return object;
  };
  const box = (w: number, h: number, d: number, material: THREE.Material) =>
    new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);

  // Lights: soft sky fill plus a key light that follows the sun when it is known.
  scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa59c, 0.55));
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.8);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(1024, 1024);
  keyLight.shadow.camera.left = -8;
  keyLight.shadow.camera.right = 8;
  keyLight.shadow.camera.top = 8;
  keyLight.shadow.camera.bottom = -8;
  keyLight.shadow.bias = -0.0005;
  scene.add(keyLight, keyLight.target);
  keyLight.target.position.set(0, TOP_Y, 0);

  // Ground with compass ring and labels.
  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(9, 72),
    mat(0xdbe3d9, { roughness: 1, envMapIntensity: 0.5 }),
  );
  ground.rotation.x = -Math.PI / 2;
  add(scene, ground, false, true);
  const ring = new THREE.Mesh(new THREE.RingGeometry(5.6, 5.68, 96), new THREE.MeshBasicMaterial({ color: 0x9fb0a7 }));
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.01;
  scene.add(ring);
  for (let bearing = 0; bearing < 360; bearing += 15) {
    const major = bearing % 90 === 0;
    const tick = new THREE.Mesh(
      new THREE.PlaneGeometry(0.05, major ? 0.5 : 0.25),
      new THREE.MeshBasicMaterial({ color: major ? 0x49605b : 0x9fb0a7 }),
    );
    const dir = bearingDir(bearing);
    tick.position.copy(dir.clone().multiplyScalar(5.64 + (major ? 0.25 : 0.12)));
    tick.position.y = 0.012;
    tick.rotation.set(-Math.PI / 2, 0, -(bearing * Math.PI) / 180);
    scene.add(tick);
  }
  for (const [text, bearing] of [["N", 0], ["E", 90], ["S", 180], ["W", 270]] as const) {
    const sprite = labelSprite(labelTexture(text, text === "N" ? "#c45050" : "#49605b"));
    sprite.position.copy(bearingDir(bearing).multiplyScalar(7.15));
    sprite.position.y = 0.45;
    scene.add(sprite);
  }

  // No-travel zone for fixed-tilt mode (305° → 55°, through north).
  const noTravelSpan = 360 - (FIXED_YAW_TRAVEL.maxDeg - FIXED_YAW_TRAVEL.minDeg);
  const noTravel = new THREE.Mesh(
    // RingGeometry theta t maps to bearing 90° - t after lying flat.
    new THREE.RingGeometry(2.25, 2.85, 48, 1,
      ((90 - (FIXED_YAW_TRAVEL.maxDeg + noTravelSpan)) * Math.PI) / 180, (noTravelSpan * Math.PI) / 180),
    new THREE.MeshBasicMaterial({ color: COLORS.noTravel, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false }),
  );
  noTravel.rotation.x = -Math.PI / 2;
  noTravel.position.set(TURNTABLE.x, TOP_Y + 0.02, TURNTABLE.z);
  noTravel.visible = false;
  scene.add(noTravel);

  // Fixed base: aluminium extrusion frame, plates, electrical box and conduit.
  const base = new THREE.Group();
  scene.add(base);
  const { width: W, depth: D, height: H, bar: B } = FRAME;
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const post = box(B, H, B, aluminium);
      post.position.set(sx * (W / 2 - B / 2), H / 2, sz * (D / 2 - B / 2));
      add(base, post);
    }
  }
  for (const y of [B / 2, H - B / 2, H - B * 2.2]) {
    for (const sz of [-1, 1]) {
      const rail = box(W, B, B, aluminium);
      rail.position.set(0, y, sz * (D / 2 - B / 2));
      add(base, rail);
    }
    for (const sx of [-1, 1]) {
      const rail = box(B, B, D - 2 * B, aluminium);
      rail.position.set(sx * (W / 2 - B / 2), y, 0);
      add(base, rail);
    }
  }
  const bottomPlate = box(W - 2 * B, 0.04, D - 2 * B, mat(COLORS.acrylicTan, { roughness: 0.6 }));
  bottomPlate.position.set(0, B + 0.02, 0);
  add(base, bottomPlate);
  const topPlate = box(W + 0.3, 0.08, D + 0.3, mat(COLORS.acrylicBlack, { roughness: 0.12, metalness: 0.2 }));
  topPlate.position.set(0, H + 0.04, 0);
  add(base, topPlate);
  const electricalBox = box(2.8, 0.95, 2.2, mat(COLORS.box, { roughness: 0.7 }));
  electricalBox.position.set(0.1, B + 0.04 + 0.475, 0.9);
  add(base, electricalBox);
  const conduit = new THREE.Mesh(
    new THREE.TorusGeometry(1.35, 0.13, 12, 48, Math.PI * 1.25),
    mat(COLORS.conduit, { roughness: 0.9 }),
  );
  conduit.rotation.set(Math.PI / 2, 0, Math.PI * 0.85);
  conduit.position.set(-0.3, H - 0.55, -0.6);
  add(base, conduit);

  // Charge controller on its grey bracket (front-left corner of the top plate).
  const controller = new THREE.Group();
  const bracket = box(1.5, 0.05, 1.15, mat(0xb9bdbc, { roughness: 0.5 }));
  controller.add(bracket);
  const body = box(1.3, 0.28, 0.9, mat(COLORS.controller, { roughness: 0.4 }));
  body.position.y = 0.16;
  controller.add(body);
  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(0.55, 0.38),
    new THREE.MeshStandardMaterial({ color: COLORS.screen, emissive: COLORS.screen, emissiveIntensity: 0.6 }),
  );
  screen.rotation.x = -Math.PI / 2;
  screen.position.set(-0.1, 0.305, 0);
  controller.add(screen);
  controller.position.set(-2.25, TOP_Y + 0.03, 2.2);
  controller.rotation.y = 0.12;
  add(base, controller);

  // Belt drive on the top plate (fixed): motor pulley, idlers and a belt loop
  // around the turntable base.
  const pulleyMat = mat(0x8d9399, { metalness: 0.7, roughness: 0.3 });
  const motorMat = mat(0x2c3236, { roughness: 0.5, emissive: COLORS.driveOn, emissiveIntensity: 0 });
  const pulleys: THREE.Group[] = [];
  const pulleySpecs = [
    { x: 2.55, z: 1.95, r: 0.28, motor: true },
    { x: 1.75, z: 2.25, r: 0.17, motor: false },
    { x: 2.75, z: 1.05, r: 0.17, motor: false },
  ];
  for (const spec of pulleySpecs) {
    const pulley = new THREE.Group();
    const wheel = new THREE.Mesh(new THREE.CylinderGeometry(spec.r, spec.r, 0.2, 28), pulleyMat);
    pulley.add(wheel);
    const marker = box(spec.r * 1.6, 0.05, 0.05, mat(0x2a2f33));
    marker.position.y = 0.11;
    pulley.add(marker);
    if (spec.motor) {
      const motor = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.5, 24), motorMat);
      motor.position.y = -0.38;
      pulley.add(motor);
    }
    pulley.position.set(spec.x, TOP_Y + 0.12, spec.z);
    add(base, pulley);
    pulleys.push(pulley);
  }
  const driveGlow = new THREE.MeshBasicMaterial({ color: COLORS.driveOn, transparent: true, opacity: 0.95 });
  const yawDriveRing = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.055, 10, 40), driveGlow);
  yawDriveRing.rotation.x = Math.PI / 2;
  yawDriveRing.position.set(pulleySpecs[0].x, TOP_Y + 0.24, pulleySpecs[0].z);
  yawDriveRing.visible = false;
  scene.add(yawDriveRing);
  const turntableCentre = new THREE.Vector3(TURNTABLE.x, TOP_Y + 0.12, TURNTABLE.z);
  const beltPoints: THREE.Vector3[] = [];
  const motor = pulleySpecs[0];
  const toMotor = Math.atan2(motor.z - TURNTABLE.z, motor.x - TURNTABLE.x);
  for (let k = 0; k <= 24; k++) {
    const a = toMotor + Math.PI / 2 + (k / 24) * Math.PI;
    beltPoints.push(new THREE.Vector3(TURNTABLE.x + Math.cos(a) * 2.06, turntableCentre.y, TURNTABLE.z + Math.sin(a) * 2.06));
  }
  for (let k = 0; k <= 12; k++) {
    const a = toMotor - Math.PI / 2 + (k / 12) * Math.PI;
    beltPoints.push(new THREE.Vector3(motor.x + Math.cos(a) * (motor.r + 0.03), turntableCentre.y, motor.z + Math.sin(a) * (motor.r + 0.03)));
  }
  const belt = new THREE.Mesh(
    new THREE.TubeGeometry(new THREE.CatmullRomCurve3(beltPoints, true), 160, 0.035, 6, true),
    mat(COLORS.belt, { roughness: 0.8 }),
  );
  add(base, belt);

  // Turntable (rotates with azimuth).
  const turntable = new THREE.Group();
  turntable.position.set(TURNTABLE.x, TOP_Y, TURNTABLE.z);
  scene.add(turntable);
  const bearingRing = new THREE.Mesh(new THREE.CylinderGeometry(2.05, 2.05, 0.16, 64), mat(0x2b3033, { roughness: 0.5 }));
  bearingRing.position.y = 0.08;
  add(turntable, bearingRing);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(1.86, 0.07, 12, 64), aluminium);
  rim.rotation.x = Math.PI / 2;
  rim.position.y = 0.2;
  add(turntable, rim);
  const disk = new THREE.Mesh(
    new THREE.CylinderGeometry(1.8, 1.8, 0.05, 64),
    new THREE.MeshPhysicalMaterial({ color: 0xdfeceb, transmission: 0.6, transparent: true, opacity: 0.55, roughness: 0.1 }),
  );
  disk.position.y = 0.2;
  add(turntable, disk, false, true);
  for (const [x, z, color] of [[0.6, 0.5, 0xf08a3c], [0.2, 0.75, 0xf08a3c], [-0.7, 0.2, 0x1a1a1a]] as const) {
    const part = box(0.4, 0.12, 0.22, mat(color, { roughness: 0.5 }));
    part.position.set(x, 0.29, z);
    add(turntable, part);
  }
  const hingeBar = box(PANEL.width * 0.9, 0.16, 0.18, aluminium);
  hingeBar.position.set(0, PANEL.hingeY - 0.08, PANEL.hingeZ);
  add(turntable, hingeBar);

  // Panel on its front hinge (tilts about the local X axis).
  const hinge = new THREE.Group();
  hinge.position.set(0, PANEL.hingeY, PANEL.hingeZ);
  turntable.add(hinge);
  const panel = new THREE.Group();
  hinge.add(panel);
  const frameThickness = 0.1;
  for (const sx of [-1, 1]) {
    const side = box(0.1, frameThickness, PANEL.length, aluminium);
    side.position.set(sx * (PANEL.width / 2 - 0.05), 0, PANEL.length / 2);
    add(panel, side);
  }
  for (const z of [0.05, PANEL.length - 0.05]) {
    const end = box(PANEL.width, frameThickness, 0.1, aluminium);
    end.position.set(0, 0, z);
    add(panel, end);
  }
  const cells = new THREE.Mesh(
    new THREE.PlaneGeometry(PANEL.width - 0.2, PANEL.length - 0.2),
    new THREE.MeshStandardMaterial({ map: cellTexture(), roughness: 0.25, metalness: 0.35, emissive: 0x1b3b8f, emissiveIntensity: 0 }),
  );
  cells.rotation.x = -Math.PI / 2;
  cells.position.set(0, 0.03, PANEL.length / 2);
  add(panel, cells);
  const backSheet = new THREE.Mesh(
    new THREE.PlaneGeometry(PANEL.width - 0.2, PANEL.length - 0.2),
    mat(COLORS.panelBack, { roughness: 0.8, side: THREE.DoubleSide }),
  );
  backSheet.rotation.x = Math.PI / 2;
  backSheet.position.set(0, -0.03, PANEL.length / 2);
  add(panel, backSheet);
  const junction = box(0.45, 0.14, 0.32, mat(0x151515));
  junction.position.set(-0.7, -0.1, PANEL.length * 0.55);
  add(panel, junction);

  // Linear actuator between the turntable and the panel back.
  const actuatorBase = new THREE.Vector3(0, 0.3, 0.75);
  const actuatorTopOnPanel = new THREE.Vector3(0, -0.05, PANEL.length * 0.72);
  const barrelMat = mat(0x9aa2a8, { metalness: 0.7, roughness: 0.3, emissive: COLORS.driveOn, emissiveIntensity: 0 });
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 1, 16), barrelMat);
  const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.045, 1, 12), mat(0xdfe3e6, { metalness: 0.9, roughness: 0.2 }));
  add(turntable, barrel);
  add(turntable, rod);
  const tiltDriveLight = new THREE.Mesh(new THREE.SphereGeometry(0.16, 16, 12), driveGlow);
  tiltDriveLight.position.copy(actuatorBase);
  tiltDriveLight.visible = false;
  turntable.add(tiltDriveLight);

  // Panel-facing arrow (green) and sun / target marker with its ray.
  const facingArrow = new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), new THREE.Vector3(), 2.2, COLORS.facing, 0.4, 0.22);
  scene.add(facingArrow);
  const sun = new THREE.Mesh(
    new THREE.SphereGeometry(0.42, 32, 16),
    new THREE.MeshBasicMaterial({ color: COLORS.sun }),
  );
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(0.75, 32, 16),
    new THREE.MeshBasicMaterial({ color: COLORS.sun, transparent: true, opacity: 0.22, depthWrite: false }),
  );
  sun.add(halo);
  scene.add(sun);
  const sunLabelTextures = { sun: labelTexture("Sun", "#906019"), manual: labelTexture("Target", "#906019") };
  const sunLabel = labelSprite(sunLabelTextures.sun, 0.55);
  scene.add(sunLabel);
  const rayGeometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
  const ray = new THREE.Line(rayGeometry, new THREE.LineDashedMaterial({ color: 0xd99a1e, dashSize: 0.25, gapSize: 0.15 }));
  scene.add(ray);

  // Pose: what telemetry says (goal) and what is drawn (smoothed toward goal).
  const shown = { azimuth: NEUTRAL.azimuthDeg, tilt: NEUTRAL.tiltDeg };
  const goal = { ...shown };
  let drives = { azimuth: false, tilt: false };
  let latest: TwinState | null = null;

  function applyPose() {
    turntable.rotation.y = (-shown.azimuth * Math.PI) / 180;
    hinge.rotation.x = (-shown.tilt * Math.PI) / 180;
    turntable.updateMatrixWorld(true);

    // Actuator: barrel from the base, rod to the panel attachment point.
    const top = actuatorTopOnPanel.clone();
    hinge.updateMatrixWorld(true);
    top.applyMatrix4(hinge.matrix); // hinge-local → turntable-local
    const dir = top.clone().sub(actuatorBase);
    const length = dir.length();
    const unit = dir.clone().normalize();
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), unit);
    const barrelLength = Math.min(length, 0.9);
    barrel.quaternion.copy(q);
    barrel.scale.set(1, barrelLength, 1);
    barrel.position.copy(actuatorBase).addScaledVector(unit, barrelLength / 2);
    rod.quaternion.copy(q);
    rod.scale.set(1, length, 1);
    rod.position.copy(actuatorBase).addScaledVector(unit, length / 2);

    // Facing arrow from the panel centre along its normal.
    const centre = new THREE.Vector3(0, 0.08, PANEL.length / 2);
    panel.localToWorld(centre);
    facingArrow.position.copy(centre);
    facingArrow.setDirection(skyDir(shown.azimuth, shown.tilt));
    if (latest?.target?.aboveHorizon) {
      const positions = rayGeometry.attributes.position as THREE.BufferAttribute;
      positions.setXYZ(0, centre.x, centre.y, centre.z);
      positions.setXYZ(1, sun.position.x, sun.position.y, sun.position.z);
      positions.needsUpdate = true;
      ray.computeLineDistances();
    }
  }

  function update(state: TwinState) {
    latest = state;
    goal.azimuth = state.azimuthDeg ?? NEUTRAL.azimuthDeg;
    goal.tilt = state.tiltDeg ?? NEUTRAL.tiltDeg;
    drives = { azimuth: state.azimuthDrive === true, tilt: state.tiltDrive === true };
    if (options.reducedMotion || state.data !== "live") {
      shown.azimuth = goal.azimuth;
      shown.tilt = goal.tilt;
    }

    const target = state.target;
    const sunVisible = !!target?.aboveHorizon;
    sun.visible = sunVisible;
    sunLabel.visible = sunVisible;
    ray.visible = sunVisible;
    if (target && sunVisible) {
      const dir = skyDir(target.azimuthDeg, target.zenithDeg);
      sun.position.copy(turntableCentre).addScaledVector(dir, SUN_DISTANCE);
      sunLabel.position.copy(sun.position).add(new THREE.Vector3(0, 0.85, 0));
      sunLabel.material.map = sunLabelTextures[target.kind];
      keyLight.position.copy(turntableCentre).addScaledVector(dir, 12);
      keyLight.intensity = 2.4;
    } else {
      keyLight.position.set(6, 10, 7);
      keyLight.intensity = 1.6;
    }
    noTravel.visible = !!state.travelLimits;
    facingArrow.visible = state.azimuthDeg !== null && state.tiltDeg !== null;
    const live = state.data === "live";
    (cells.material as THREE.MeshStandardMaterial).emissiveIntensity =
      live && state.powerW !== null ? Math.min(0.35, state.powerW / 30) : 0;
    applyPose();
  }

  const views: Record<TwinView, THREE.Vector3> = {
    perspective: new THREE.Vector3(11.5, 9.8, 14),
    top: new THREE.Vector3(0.01, 22, 0.01),
    front: new THREE.Vector3(0, 3.8, 18),
  };
  function setView(view: TwinView) {
    camera.position.copy(views[view]);
    controls.target.set(0, view === "top" ? 0 : 2.7, 0);
    controls.update();
  }
  setView("perspective");

  function resize() {
    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    // Narrow (phone) views crop the sides, so widen the view instead.
    camera.zoom = Math.min(1, Math.max(0.62, camera.aspect * 0.78));
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();

  let frame = 0;
  let last = performance.now();
  function tick(now: number) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (!options.reducedMotion) {
      // Smooth between 1 s telemetry samples; never past the reported value.
      const k = 1 - Math.exp(-dt * 6);
      shown.azimuth = (shown.azimuth + shortestTurn(shown.azimuth, goal.azimuth) * k + 360) % 360;
      shown.tilt += (goal.tilt - shown.tilt) * k;
      if (drives.azimuth) for (const pulley of pulleys) pulley.rotation.y += dt * 4;
    }
    motorMat.emissiveIntensity = drives.azimuth ? 0.9 : 0;
    barrelMat.emissiveIntensity = drives.tilt ? 0.6 : 0;
    yawDriveRing.visible = drives.azimuth;
    tiltDriveLight.visible = drives.tilt;
    driveGlow.opacity = options.reducedMotion ? 0.95 : 0.65 + 0.3 * Math.sin(now / 180);
    applyPose();
    controls.update();
    renderer.render(scene, camera);
    frame = requestAnimationFrame(tick);
  }
  frame = requestAnimationFrame(tick);

  function dispose() {
    cancelAnimationFrame(frame);
    observer.disconnect();
    controls.dispose();
    scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      mesh.geometry?.dispose();
      const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
      for (const material of materials) {
        (material as THREE.MeshStandardMaterial).map?.dispose();
        material.dispose();
      }
    });
    environment.dispose();
    pmrem.dispose();
    sunLabelTextures.sun.dispose();
    sunLabelTextures.manual.dispose();
    renderer.dispose();
    renderer.domElement.remove();
  }

  return { update, setView, dispose };
}
