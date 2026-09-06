import type { Creative, Planogram, Sku } from "@/contracts/planogram.schema";
import { AdSlot } from "@/store/AdSlot";
import { ProductSlot } from "@/store/ProductSlot";
import {
  BAY_DEPTH_M,
  SHELF_BOARD_DEPTH_M,
  SHELF_BOARD_THICKNESS_M,
  adSlotCenter,
  adSlotSize,
  bayCenterX,
  isFlatAd,
  shelfBoardCenter,
  slotCenter,
  slotSize,
} from "@/store/geometry";
import { BOARD_COLOR, CARCASS_COLOR, EMPTY_SPACE_COLOR } from "@/store/palette";

export interface BayProps {
  planogram: Planogram;
  bayIndex: number;
  skus: Map<string, Sku>;
  creatives: Map<string, Creative>;
  hoveredSlotId: string | null;
  onSlotEnter: (slotId: string) => void;
  onSlotLeave: (slotId: string) => void;
  onSlotSelect: (slotId: string) => void;
}

/** One shelf bay: carcass box, shelf boards, product facings and ad fixtures. */
export function Bay({
  planogram,
  bayIndex,
  skus,
  creatives,
  hoveredSlotId,
  onSlotEnter,
  onSlotLeave,
  onSlotSelect,
}: BayProps) {
  const bay = planogram.bays[bayIndex];
  const centerX = bayCenterX(planogram, bayIndex);

  return (
    <group>
      <mesh position={[centerX, bay.height_m / 2, 0]}>
        <boxGeometry args={[bay.width_m, bay.height_m, BAY_DEPTH_M]} />
        <meshStandardMaterial color={CARCASS_COLOR} />
      </mesh>

      {bay.shelves.map((shelf) => {
        const board = shelfBoardCenter(planogram, bayIndex, shelf);
        return (
          <group key={shelf.shelf_id}>
            <mesh position={[board.x, board.y, board.z]}>
              <boxGeometry
                args={[bay.width_m, SHELF_BOARD_THICKNESS_M, SHELF_BOARD_DEPTH_M]}
              />
              <meshStandardMaterial color={BOARD_COLOR} />
            </mesh>

            {shelf.slots.map((slot) => {
              const center = slotCenter(planogram, bayIndex, shelf, slot);
              const size = slotSize(slot);
              const sku = slot.sku_id === null ? undefined : skus.get(slot.sku_id);

              // An empty slot is real shelf space: a visible gap, and never a
              // hover or click target. Same colour as an unbooked ad fixture,
              // because they are the same statement about the planogram — see
              // `palette.ts`.
              if (!sku) {
                return (
                  <mesh key={slot.slot_id} position={[center.x, center.y, center.z]}>
                    <planeGeometry args={[size.w, size.h]} />
                    <meshStandardMaterial
                      color={EMPTY_SPACE_COLOR}
                      polygonOffset
                      polygonOffsetFactor={-1}
                      polygonOffsetUnits={-1}
                    />
                  </mesh>
                );
              }

              return (
                <ProductSlot
                  key={slot.slot_id}
                  slot={slot}
                  sku={sku}
                  center={center}
                  size={size}
                  hovered={hoveredSlotId === slot.slot_id}
                  onEnter={onSlotEnter}
                  onLeave={onSlotLeave}
                  onSelect={onSlotSelect}
                />
              );
            })}
          </group>
        );
      })}

      {bay.ad_slots.map((ad) => (
        <AdSlot
          key={ad.ad_slot_id}
          ad={ad}
          creative={ad.creative_id === null ? null : creatives.get(ad.creative_id) ?? null}
          center={adSlotCenter(planogram, bayIndex, ad)}
          size={adSlotSize(planogram, bayIndex, ad)}
          flat={isFlatAd(ad)}
        />
      ))}
    </group>
  );
}
