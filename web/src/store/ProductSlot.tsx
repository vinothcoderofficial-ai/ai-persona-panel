import { useMemo } from "react";
import * as THREE from "three";
import { useTexture } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import type { Sku, Slot } from "@/contracts/planogram.schema";
import type { Size2, Vec3 } from "@/store/geometry";

/** Facings are tiled across the slot with a small gap so packs read separately. */
const FACING_GAP_FRACTION = 0.06;
/** One transparent plane in front of the facings carries the pointer events. */
const HIT_PLANE_Z_OFFSET = 0.002;

export interface ProductSlotProps {
  slot: Slot;
  sku: Sku;
  center: Vec3;
  size: Size2;
  hovered: boolean;
  onEnter: (slotId: string) => void;
  onLeave: (slotId: string) => void;
  onSelect: (slotId: string) => void;
}

export function ProductSlot({
  slot,
  sku,
  center,
  size,
  hovered,
  onEnter,
  onLeave,
  onSelect,
}: ProductSlotProps) {
  const texture = useTexture(sku.texture_url, (loaded) => {
    for (const map of Array.isArray(loaded) ? loaded : [loaded]) {
      map.colorSpace = THREE.SRGBColorSpace;
    }
  });

  const facings = Math.max(1, Math.round(slot.facings));
  const facingWidth = size.w / facings;
  const offsets = useMemo(
    () =>
      Array.from({ length: facings }, (_, i) => -size.w / 2 + (i + 0.5) * facingWidth),
    [facings, facingWidth, size.w],
  );

  const enter = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    onEnter(slot.slot_id);
  };
  const leave = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    onLeave(slot.slot_id);
  };
  const select = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    onSelect(slot.slot_id);
  };

  return (
    <group position={[center.x, center.y, center.z]}>
      {offsets.map((offset, index) => (
        <mesh key={index} position={[offset, 0, 0]}>
          <planeGeometry args={[facingWidth * (1 - FACING_GAP_FRACTION), size.h]} />
          <meshStandardMaterial
            map={texture}
            emissive={hovered ? "#ffffff" : "#000000"}
            emissiveIntensity={hovered ? 0.28 : 0}
            polygonOffset
            polygonOffsetFactor={-2}
            polygonOffsetUnits={-2}
          />
        </mesh>
      ))}
      <mesh
        position={[0, 0, HIT_PLANE_Z_OFFSET]}
        onPointerOver={enter}
        onPointerOut={leave}
        onClick={select}
      >
        <planeGeometry args={[size.w, size.h]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  );
}
