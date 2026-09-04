import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import type { Planogram } from "@/contracts/planogram.schema";
import { buildScreenRects, type ScreenRect } from "@/store/SlotMapper";

/** SPEC M1: the camera lerps between shelf stations in 600 ms. */
export const STATION_LERP_MS = 600;

export interface StationControllerProps {
  planogram: Planogram;
  stationIndex: number;
  /** Arrow keys step the station; the on-screen arrows call the same handler. */
  onStep: (delta: number) => void;
  onEnter: (bayId: string) => void;
  onExit: (bayId: string) => void;
  onRects: (rects: ScreenRect[]) => void;
}

interface Lerp {
  fromPos: THREE.Vector3;
  toPos: THREE.Vector3;
  fromLook: THREE.Vector3;
  toLook: THREE.Vector3;
  startedAt: number;
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) * (-2 * t + 2)) / 2;
}

/**
 * Drives the fixed station camera. There is no free roam: webcam gaze is
 * unusable while the camera moves, so the camera only ever rests at a station.
 */
export function StationController({
  planogram,
  stationIndex,
  onStep,
  onEnter,
  onExit,
  onRects,
}: StationControllerProps) {
  const camera = useThree((state) => state.camera);
  const size = useThree((state) => state.size);

  const lookAt = useRef(new THREE.Vector3());
  const lerp = useRef<Lerp | null>(null);
  const isFirstStation = useRef(true);

  const bay = planogram.bays[stationIndex];
  const station = bay.station;

  const publishRects = useCallback(() => {
    onRects(buildScreenRects(planogram, camera, size.width, size.height));
  }, [camera, onRects, planogram, size.height, size.width]);

  const publishRectsRef = useRef(publishRects);
  publishRectsRef.current = publishRects;

  // Snap to the opening station, lerp to every station after it.
  useLayoutEffect(() => {
    const toPos = new THREE.Vector3(...station.camera_pos);
    const toLook = new THREE.Vector3(...station.look_at);

    if (isFirstStation.current) {
      isFirstStation.current = false;
      camera.position.copy(toPos);
      lookAt.current.copy(toLook);
      camera.lookAt(lookAt.current);
      camera.updateMatrixWorld();
      publishRectsRef.current();
      return;
    }

    lerp.current = {
      fromPos: camera.position.clone(),
      toPos,
      fromLook: lookAt.current.clone(),
      toLook,
      startedAt: performance.now(),
    };
  }, [camera, station]);

  useFrame(() => {
    const move = lerp.current;
    if (move === null) return;

    const t = Math.min(1, (performance.now() - move.startedAt) / STATION_LERP_MS);
    const eased = easeInOut(t);
    camera.position.lerpVectors(move.fromPos, move.toPos, eased);
    lookAt.current.lerpVectors(move.fromLook, move.toLook, eased);
    camera.lookAt(lookAt.current);
    camera.updateMatrixWorld();

    if (t >= 1) {
      lerp.current = null;
      publishRectsRef.current();
    }
  });

  // Rectangles are stale after a resize; a running lerp republishes on arrival.
  useEffect(() => {
    if (lerp.current === null) publishRects();
  }, [publishRects]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        onStep(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        onStep(1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onStep]);

  // Cleanup runs before the next effect, so a change emits exit then enter.
  useEffect(() => {
    onEnter(bay.bay_id);
    return () => onExit(bay.bay_id);
  }, [bay.bay_id, onEnter, onExit]);

  return null;
}
