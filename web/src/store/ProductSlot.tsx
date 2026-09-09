import * as THREE from "three";
import { useTexture } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import type { Sku, Slot } from "@/contracts/planogram.schema";
import type { Size2, Vec3 } from "@/store/geometry";
import { labToCss } from "@/store/palette";

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
  const facings = Math.max(1, Math.round(slot.facings));
  const facingWidth = size.w / facings;
  const offsets = Array.from(
    { length: facings },
    (_, i) => -size.w / 2 + (i + 0.5) * facingWidth,
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
      {offsets.map((offset, index) =>
        sku.texture_url ? (
          <TexturedFacing
            key={index}
            url={sku.texture_url}
            offset={offset}
            width={facingWidth * (1 - FACING_GAP_FRACTION)}
            height={size.h}
            hovered={hovered}
          />
        ) : (
          <mesh key={index} position={[offset, 0, 0]}>
            <planeGeometry args={[facingWidth * (1 - FACING_GAP_FRACTION), size.h]} />
            <meshStandardMaterial
              color={labToCss(sku.color_lab)}
              emissive={hovered ? "#ffffff" : "#000000"}
              emissiveIntensity={hovered ? 0.28 : 0}
              polygonOffset
              polygonOffsetFactor={-2}
              polygonOffsetUnits={-2}
            />
          </mesh>
        ),
      )}
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

interface TexturedFacingProps {
  url: string;
  offset: number;
  width: number;
  height: number;
  hovered: boolean;
}

/**
 * One facing drawn from a pack shot.
 *
 * Separate from `ProductSlot` for one reason, and it is a rule rather than a
 * preference: `useTexture` is a hook, hooks cannot be called conditionally,
 * and there are SKUs with no texture to load. `vision/planogram.py` emits
 * `texture_url: ""` for every product it reads off a video - a classical
 * pipeline measures position, size and colour, and there is no pack shot in
 * the frame to point at. Called with that empty string drei's loader throws,
 * and from `ProductSlot`'s own body there is nothing below to catch it: it
 * unwinds past Suspense into the scene's error boundary and takes the whole
 * store down with it.
 *
 * Pushing the load down here means the component that does it is only ever
 * created when there is something to load, and the textureless case never
 * reaches a loader at all.
 */
function TexturedFacing({ url, offset, width, height, hovered }: TexturedFacingProps) {
  const texture = useTexture(url, (loaded) => {
    for (const map of Array.isArray(loaded) ? loaded : [loaded]) {
      map.colorSpace = THREE.SRGBColorSpace;
    }
  });

  return (
    <mesh position={[offset, 0, 0]}>
      <planeGeometry args={[width, height]} />
      <meshStandardMaterial
        map={texture}
        emissive={hovered ? "#ffffff" : "#000000"}
        emissiveIntensity={hovered ? 0.28 : 0}
        polygonOffset
        polygonOffsetFactor={-2}
        polygonOffsetUnits={-2}
      />
    </mesh>
  );
}
