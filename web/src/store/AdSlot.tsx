import * as THREE from "three";
import { useTexture } from "@react-three/drei";
import type { AdSlot as AdSlotData, Creative } from "@/contracts/planogram.schema";
import type { Size2, Vec3 } from "@/store/geometry";

export interface AdSlotProps {
  ad: AdSlotData;
  creative: Creative | null;
  center: Vec3;
  size: Size2;
  flat: boolean;
}

function CreativePlane({ url, size }: { url: string; size: Size2 }) {
  const texture = useTexture(url, (loaded) => {
    for (const map of Array.isArray(loaded) ? loaded : [loaded]) {
      map.colorSpace = THREE.SRGBColorSpace;
    }
  });
  return (
    <>
      <planeGeometry args={[size.w, size.h]} />
      <meshStandardMaterial map={texture} />
    </>
  );
}

/**
 * An ad fixture: a textured plane when a creative is booked, otherwise a blank
 * fixture. Neither is a hover or click target — ad attention is measured by
 * gaze through SlotMapper, not by clicking.
 */
export function AdSlot({ ad, creative, center, size, flat }: AdSlotProps) {
  return (
    <mesh
      position={[center.x, center.y, center.z]}
      rotation={flat ? [-Math.PI / 2, 0, 0] : [0, 0, 0]}
    >
      {creative ? (
        <CreativePlane url={creative.texture_url} size={size} />
      ) : (
        <>
          <planeGeometry args={[size.w, size.h]} />
          <meshStandardMaterial color="#d8d8d2" />
        </>
      )}
    </mesh>
  );
}
