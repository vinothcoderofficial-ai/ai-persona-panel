import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { Canvas } from "@react-three/fiber";
import type { Creative, Planogram, Sku, Slot } from "@/contracts/planogram.schema";
import { finishSession } from "@/api/client";
import { CursorTracker, type CursorDwell } from "@/capture/CursorTracker";
import { FixationFilter, fixationPayload, type Fixation } from "@/capture/FixationFilter";
import type { GazeTracker } from "@/capture/GazeTracker";
import type { EventSink } from "@/capture/SessionSocket";
import { Bay } from "@/store/Bay";
import { StationController } from "@/store/StationController";
import type { ScreenRect } from "@/store/SlotMapper";
import { CAMERA_FAR, CAMERA_FOV, CAMERA_NEAR, bayCenterX } from "@/store/geometry";

/** SPEC M1: clicking a product opens a 1.5x zoom card with its price. */
const CARD_ZOOM = 1.5;
const CARD_MARGIN_PX = 12;
const CARD_TEXT_HEIGHT_PX = 150;

interface SlotEntry {
  slot: Slot;
  sku: Sku;
}

interface CartLine {
  key: number;
  slot_id: string;
  sku_id: string;
  name: string;
  price: number;
}

export interface PlanogramSceneProps {
  planogram: Planogram;
  logger: EventSink;
  /**
   * The calibrated tracker a `webcam` session brings with it from CaptureFlow.
   * Null or absent for `cursor_only`. This component owns it from here: it
   * releases the camera at checkout and on unmount.
   */
  tracker?: GazeTracker | null;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function rectCenter(rect: ScreenRect): { x: number; y: number } {
  return { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The shop screen: the 3D store plus the shopper's own controls.
 *
 * It renders the resolved planogram exactly as the server sent it — there is no
 * client-side resolve — and it deliberately shows no gaze dot: that belongs to
 * the spectator view, because shoppers stare at their own dot. A `webcam`
 * session streams `gaze` and `fixation` events from here while the person
 * shops; nothing about them is drawn on this screen.
 */
export function PlanogramScene({ planogram, logger, tracker }: PlanogramSceneProps) {
  const [stationIndex, setStationIndex] = useState(0);
  const [rects, setRects] = useState<ScreenRect[]>([]);
  const [hoveredSlotId, setHoveredSlotId] = useState<string | null>(null);
  const [cardSlotId, setCardSlotId] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [checkedOut, setCheckedOut] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);
  const nextLineKey = useRef(1);

  const cursorRef = useRef<CursorTracker | null>(null);
  if (cursorRef.current === null) cursorRef.current = new CursorTracker();
  const cursor = cursorRef.current;

  const skus = useMemo(
    () => new Map<string, Sku>(planogram.skus.map((sku) => [sku.sku_id, sku])),
    [planogram],
  );
  const creatives = useMemo(
    () =>
      new Map<string, Creative>(
        planogram.creatives.map((creative) => [creative.creative_id, creative]),
      ),
    [planogram],
  );
  const slotIndex = useMemo(() => {
    const index = new Map<string, SlotEntry>();
    for (const bay of planogram.bays) {
      for (const shelf of bay.shelves) {
        for (const slot of shelf.slots) {
          const sku = slot.sku_id === null ? undefined : skus.get(slot.sku_id);
          if (sku) index.set(slot.slot_id, { slot, sku });
        }
      }
    }
    return index;
  }, [planogram, skus]);

  const stationId = planogram.bays[stationIndex].bay_id;

  // Memoised: R3F reapplies the camera options whenever this object changes,
  // which would fight the station lerp.
  const cameraOptions = useMemo(
    () => ({
      fov: CAMERA_FOV,
      near: CAMERA_NEAR,
      far: CAMERA_FAR,
      position: planogram.bays[0].station.camera_pos,
    }),
    [planogram],
  );
  const aisleCenterX = useMemo(
    () => bayCenterX(planogram, planogram.bays.length - 1) / 2,
    [planogram],
  );

  const step = useCallback(
    (delta: number) => {
      setStationIndex((index) => clamp(index + delta, 0, planogram.bays.length - 1));
    },
    [planogram],
  );
  const onStationEnter = useCallback(
    (bayId: string) => logger.log("station_enter", bayId, {}),
    [logger],
  );
  const onStationExit = useCallback(
    (bayId: string) => logger.log("station_exit", bayId, {}),
    [logger],
  );

  useEffect(() => {
    document.body.style.cursor = hoveredSlotId === null ? "auto" : "pointer";
    return () => {
      document.body.style.cursor = "auto";
    };
  }, [hoveredSlotId]);

  const logDwell = (dwell: CursorDwell | null) => {
    if (dwell !== null) logger.log("cursor_dwell", stationId, dwell);
  };

  // cursor_dwell is 0.7 of the fused attention in analytics/fusion.py, so it is
  // measured against the same SlotMapper rectangles as everything else. Time
  // spent over the HUD or the cart panel is not time spent on a shelf.
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const now = performance.now();
    logDwell(
      event.target instanceof HTMLCanvasElement
        ? cursor.sample(rects, event.clientX, event.clientY, now)
        : cursor.end(now),
    );
  };

  // A dwell cannot span two stations, and must not outlive the session.
  useEffect(() => {
    return () => {
      const dwell = cursor.end(performance.now());
      if (dwell !== null) logger.log("cursor_dwell", stationId, dwell);
    };
  }, [cursor, logger, stationId]);

  // Read through refs rather than through the effect's dependencies: rebuilding
  // the subscription every time the camera lerps or the station changes would
  // throw away FixationFilter's open run, and with it the fixation it was in
  // the middle of measuring.
  const rectsRef = useRef<ScreenRect[]>(rects);
  rectsRef.current = rects;
  const stationIdRef = useRef(stationId);
  stationIdRef.current = stationId;
  const stopGaze = useRef<(() => void) | null>(null);

  /**
   * The webcam pipeline, and the only place gaze becomes events.
   *
   * Everything upstream of `logger.log` stays in this browser: WebGazer's
   * predictions are turned into `{x, y, conf, t}` inside GazeTracker, filtered
   * into fixations inside FixationFilter, and no frame, eye patch or model ever
   * reaches the network. No dot is drawn here either - the shopper's screen
   * stays clean and the spectator view renders the trail from the `gaze` events
   * instead.
   */
  useEffect(() => {
    if (tracker === null || tracker === undefined) return undefined;

    const filter = new FixationFilter();
    const record = (fixations: Fixation[]) => {
      for (const fixation of fixations) {
        logger.log(
          "fixation",
          stationIdRef.current,
          fixationPayload(fixation, rectsRef.current),
        );
      }
    };

    const unsubscribe = tracker.subscribe((sample) => {
      logger.log("gaze", stationIdRef.current, {
        x: sample.x,
        y: sample.y,
        conf: sample.conf,
      });
      record(filter.push(sample));
    });

    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      unsubscribe();
      // The fixation still open when shopping stops is a real fixation.
      record(filter.end());
      // This component owns the camera from the moment CaptureFlow handed the
      // tracker over. Checkout or unmount, it goes back here.
      tracker.stop();
    };

    stopGaze.current = release;
    return () => {
      release();
      stopGaze.current = null;
    };
  }, [logger, tracker]);

  const onSlotEnter = (slotId: string) => {
    setHoveredSlotId(slotId);
    const entry = slotIndex.get(slotId);
    if (entry) {
      logger.log("hover", stationId, { sku_id: entry.sku.sku_id, slot_id: slotId });
    }
  };
  const onSlotLeave = (slotId: string) => {
    setHoveredSlotId((current) => (current === slotId ? null : current));
  };
  const onSlotSelect = (slotId: string) => {
    const entry = slotIndex.get(slotId);
    if (!entry) return;
    setCardSlotId(slotId);
    logger.log("pickup", stationId, { sku_id: entry.sku.sku_id, slot_id: slotId });
  };

  const addToCart = (entry: SlotEntry) => {
    setCart((lines) => [
      ...lines,
      {
        key: nextLineKey.current++,
        slot_id: entry.slot.slot_id,
        sku_id: entry.sku.sku_id,
        name: entry.sku.name,
        price: entry.sku.price,
      },
    ]);
    logger.log("add_to_cart", stationId, {
      sku_id: entry.sku.sku_id,
      slot_id: entry.slot.slot_id,
    });
    setCardSlotId(null);
  };

  const removeLine = (line: CartLine) => {
    setCart((lines) => lines.filter((candidate) => candidate.key !== line.key));
    logger.log("remove", stationId, { sku_id: line.sku_id, slot_id: line.slot_id });
  };

  const checkout = async () => {
    if (checkedOut) return;
    logDwell(cursor.end(performance.now()));
    // Measurement over: the last fixation is recorded and the camera is handed
    // back before the session is closed on the server.
    stopGaze.current?.();
    logger.log("checkout", stationId, {});
    setCheckedOut(true);
    setCardSlotId(null);
    await logger.flush();
    try {
      await finishSession(logger.sessionId, { ended_at: new Date().toISOString() });
    } catch (error) {
      setFinishError(errorMessage(error));
    }
  };

  const cardEntry = cardSlotId === null ? null : slotIndex.get(cardSlotId) ?? null;
  const cardRect =
    cardSlotId === null ? undefined : rects.find((rect) => rect.slot_id === cardSlotId);
  const cardWidth = (cardRect?.w ?? 200) * CARD_ZOOM;
  const cardImageHeight = (cardRect?.h ?? 140) * CARD_ZOOM;
  const anchor = cardRect
    ? rectCenter(cardRect)
    : { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  const cardLeft = clamp(
    anchor.x - cardWidth / 2,
    CARD_MARGIN_PX,
    Math.max(CARD_MARGIN_PX, window.innerWidth - cardWidth - CARD_MARGIN_PX),
  );
  const cardTop = clamp(
    anchor.y - cardImageHeight / 2,
    CARD_MARGIN_PX,
    Math.max(
      CARD_MARGIN_PX,
      window.innerHeight - cardImageHeight - CARD_TEXT_HEIGHT_PX,
    ),
  );
  const cartTotal = cart.reduce((sum, line) => sum + line.price, 0);

  return (
    <div style={rootStyle} onPointerMove={onPointerMove}>
      <Canvas camera={cameraOptions}>
        <color attach="background" args={["#151920"]} />
        <ambientLight intensity={0.75} />
        <directionalLight position={[2.5, 5, 4]} intensity={1.1} />
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[aisleCenterX, 0, 0]}>
          <planeGeometry args={[40, 40]} />
          <meshStandardMaterial color="#31363f" />
        </mesh>
        <StationController
          planogram={planogram}
          stationIndex={stationIndex}
          onStep={step}
          onEnter={onStationEnter}
          onExit={onStationExit}
          onRects={setRects}
        />
        <Suspense fallback={null}>
          {planogram.bays.map((bay, bayIndex) => (
            <Bay
              key={bay.bay_id}
              planogram={planogram}
              bayIndex={bayIndex}
              skus={skus}
              creatives={creatives}
              hoveredSlotId={hoveredSlotId}
              onSlotEnter={onSlotEnter}
              onSlotLeave={onSlotLeave}
              onSlotSelect={onSlotSelect}
            />
          ))}
        </Suspense>
      </Canvas>

      <div style={hudStyle}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>{planogram.name}</div>
        <div style={{ opacity: 0.7 }}>
          Station {stationIndex + 1} of {planogram.bays.length} — bay {stationId}
        </div>
        <div style={{ opacity: 0.55, marginTop: 4 }}>
          Left and right arrow keys move between bays.
        </div>
      </div>

      <button
        type="button"
        aria-label="Previous bay"
        style={{ ...arrowStyle, left: 16 }}
        disabled={stationIndex === 0}
        onClick={() => step(-1)}
      >
        {"‹"}
      </button>
      <button
        type="button"
        aria-label="Next bay"
        style={{ ...arrowStyle, right: 16 }}
        disabled={stationIndex === planogram.bays.length - 1}
        onClick={() => step(1)}
      >
        {"›"}
      </button>

      {cardEntry && (
        <div style={{ ...cardStyle, left: cardLeft, top: cardTop, width: cardWidth }}>
          <img
            src={cardEntry.sku.texture_url}
            alt={cardEntry.sku.name}
            style={{ width: "100%", height: cardImageHeight, objectFit: "fill" }}
          />
          <div style={{ fontWeight: 600, marginTop: 8 }}>{cardEntry.sku.name}</div>
          <div style={{ opacity: 0.7, fontSize: 13 }}>
            {cardEntry.sku.brand} — {cardEntry.sku.category}
          </div>
          <div style={{ marginTop: 6, fontSize: 18 }}>
            {cardEntry.sku.price.toFixed(2)}
            {cardEntry.sku.promo && <span style={promoStyle}>PROMO</span>}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button
              type="button"
              style={primaryButtonStyle}
              onClick={() => addToCart(cardEntry)}
            >
              Add to cart
            </button>
            <button
              type="button"
              style={buttonStyle}
              onClick={() => setCardSlotId(null)}
            >
              Close
            </button>
          </div>
        </div>
      )}

      <div style={cartStyle}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Cart ({cart.length})</div>
        {cart.length === 0 && <div style={{ opacity: 0.6 }}>Nothing picked up yet.</div>}
        {cart.map((line) => (
          <div key={line.key} style={cartLineStyle}>
            <span style={{ flex: 1 }}>{line.name}</span>
            <span style={{ opacity: 0.8 }}>{line.price.toFixed(2)}</span>
            <button
              type="button"
              style={smallButtonStyle}
              onClick={() => removeLine(line)}
            >
              Remove
            </button>
          </div>
        ))}
        <div style={{ display: "flex", marginTop: 8, alignItems: "center" }}>
          <span style={{ flex: 1, opacity: 0.8 }}>Total {cartTotal.toFixed(2)}</span>
          <button
            type="button"
            style={primaryButtonStyle}
            onClick={() => void checkout()}
          >
            Checkout
          </button>
        </div>
      </div>

      {checkedOut && (
        <div style={doneOverlayStyle}>
          <div style={donePanelStyle}>
            <div style={{ fontSize: 20, fontWeight: 600 }}>Checkout complete</div>
            <div style={{ marginTop: 8, opacity: 0.8 }}>
              {cart.length} item{cart.length === 1 ? "" : "s"} — total{" "}
              {cartTotal.toFixed(2)}
            </div>
            {finishError !== null && (
              <div style={{ marginTop: 12, color: "#ff9a8a" }}>
                The session could not be closed on the server: {finishError}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const rootStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "#151920",
  color: "#e8eaed",
  fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
  fontSize: 14,
};

const hudStyle: CSSProperties = {
  position: "absolute",
  top: 16,
  left: 16,
  padding: "10px 14px",
  borderRadius: 8,
  background: "rgba(12,14,18,0.72)",
  pointerEvents: "none",
};

const arrowStyle: CSSProperties = {
  position: "absolute",
  top: "50%",
  transform: "translateY(-50%)",
  width: 44,
  height: 64,
  fontSize: 26,
  color: "#e8eaed",
  background: "rgba(12,14,18,0.72)",
  border: "1px solid rgba(232,234,237,0.25)",
  borderRadius: 8,
  cursor: "pointer",
};

const buttonStyle: CSSProperties = {
  padding: "6px 12px",
  color: "#e8eaed",
  background: "rgba(232,234,237,0.12)",
  border: "1px solid rgba(232,234,237,0.25)",
  borderRadius: 6,
  cursor: "pointer",
  font: "inherit",
};

const primaryButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: "#3b82f6",
  borderColor: "#3b82f6",
  color: "#0b1220",
  fontWeight: 600,
};

const smallButtonStyle: CSSProperties = {
  ...buttonStyle,
  padding: "2px 8px",
  fontSize: 12,
};

const cardStyle: CSSProperties = {
  position: "absolute",
  padding: 12,
  borderRadius: 10,
  background: "rgba(12,14,18,0.94)",
  border: "1px solid rgba(232,234,237,0.2)",
  boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
};

const promoStyle: CSSProperties = {
  marginLeft: 8,
  padding: "2px 6px",
  borderRadius: 4,
  background: "#f59e0b",
  color: "#1a1a1a",
  fontSize: 11,
  fontWeight: 700,
};

const cartStyle: CSSProperties = {
  position: "absolute",
  right: 16,
  bottom: 16,
  width: 300,
  maxHeight: "45vh",
  overflowY: "auto",
  padding: 12,
  borderRadius: 10,
  background: "rgba(12,14,18,0.88)",
  border: "1px solid rgba(232,234,237,0.2)",
};

const cartLineStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  padding: "3px 0",
};

const doneOverlayStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(8,10,14,0.72)",
};

const donePanelStyle: CSSProperties = {
  padding: "24px 32px",
  borderRadius: 12,
  background: "#12151b",
  border: "1px solid rgba(232,234,237,0.2)",
  textAlign: "center",
};
