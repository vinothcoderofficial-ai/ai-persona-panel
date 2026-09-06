import * as THREE from "three";
import { useTexture } from "@react-three/drei";
import type { AdSlot as AdSlotData, Creative } from "@/contracts/planogram.schema";
import {
  EMPTY_AD_LIP_RELIEF_M,
  emptyAdFixtureParts,
  type Size2,
  type Vec3,
} from "@/store/geometry";
import { CARCASS_COLOR, EMPTY_SPACE_COLOR } from "@/store/palette";

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
 * An ad fixture: a textured plane when a creative is booked, an empty poster
 * holder when one is not. Neither is a hover or click target — ad attention is
 * measured by gaze through SlotMapper, not by clicking.
 *
 * Why an unbooked fixture is drawn the way it is
 * ----------------------------------------------
 * On variant A only `B3_ENDCAP` carries a creative, and `D.json` is a control
 * arm with none anywhere, so an unbooked fixture is not a missing asset — it is
 * a condition of the experiment, and two of the three fixtures are in it on the
 * arm the demo opens on. Drawn as a single blank light-grey plane it read as
 * the opposite: a panel whose texture had failed to load, which is exactly what
 * a broken `ProductSlot` looks like two shelves below it.
 *
 * So it is drawn as furniture instead. The interior is `EMPTY_SPACE_COLOR`, the
 * same dark slate `Bay.tsx` gives a shelf position with `sku_id: null`, framed
 * by a lip in the carcass grey: a fixture with its housing showing, holding
 * nothing. The store then says one thing about empty space rather than two, and
 * nothing here carries imagery, so it cannot be mistaken for a creative at any
 * distance.
 *
 * Deliberately built from colours that are already everywhere in the scene.
 * `sim/saliency.py` skips a creative-less ad slot outright — "an ad slot with
 * no creative shows nothing, so nobody looks at it" — so the synthetic panel
 * scores it at zero. An empty fixture that shouted would pull real gaze the
 * panel it is being compared against has no way to predict.
 *
 * It also keeps the booked fixture's exact footprint (`emptyAdFixtureParts`),
 * so booking `AD_1` changes the creative and never how much of the shelf behind
 * the fixture the shopper can see. `web/tests/adFixtureEmpty.test.tsx` pins
 * both of those.
 */
export function AdSlot({ creative, center, size, flat }: AdSlotProps) {
  const position: [number, number, number] = [center.x, center.y, center.z];
  const rotation: [number, number, number] = flat ? [-Math.PI / 2, 0, 0] : [0, 0, 0];

  if (creative) {
    return (
      <mesh position={position} rotation={rotation}>
        <CreativePlane url={creative.texture_url} size={size} />
      </mesh>
    );
  }

  // A group rather than a mesh, because the frame is five surfaces: the local
  // `+z` the lip stands proud along is the group's own axis, which the same
  // rotation turns into "up out of the floor" for a decal and "toward the
  // shopper" for every upright fixture, with no second case to write.
  const parts = emptyAdFixtureParts(size);
  return (
    <group position={position} rotation={rotation}>
      <mesh>
        <planeGeometry args={[parts.panel.w, parts.panel.h]} />
        <meshStandardMaterial color={EMPTY_SPACE_COLOR} />
      </mesh>
      {parts.lip.map((bar, index) => (
        <mesh key={index} position={[bar.x, bar.y, EMPTY_AD_LIP_RELIEF_M]}>
          <planeGeometry args={[bar.w, bar.h]} />
          <meshStandardMaterial color={CARCASS_COLOR} />
        </mesh>
      ))}
    </group>
  );
}
