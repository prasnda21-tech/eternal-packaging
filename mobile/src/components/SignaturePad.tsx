import React, { useMemo, useRef, useState } from "react";
import { PanResponder, StyleSheet, Text, View } from "react-native";
import { colors, space, typography } from "../theme";
import { GhostButton } from "./Ui";
import { Stroke, StrokePoint, hasDrawableStrokes, strokesToPngDataUrl } from "../utils/signaturePng";

type Props = {
  disabled?: boolean;
  height?: number;
  testID?: string;
  onChange: (dataUrl: string | null) => void;
  onDrawingChange?: (drawing: boolean) => void;
  showClear?: boolean;
};

export default function SignaturePad({
  disabled = false,
  height = 168,
  testID = "signature-pad",
  onChange,
  onDrawingChange,
  showClear = true,
}: Props) {
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [size, setSize] = useState({ w: 0, h: height });
  const live = useRef<Stroke[]>([]);
  const current = useRef<Stroke>([]);
  const drawing = useRef(false);

  const emit = (next: Stroke[]) => {
    if (!hasDrawableStrokes(next) || size.w < 8) {
      onChange(null);
      return;
    }
    onChange(strokesToPngDataUrl(next, size.w, size.h));
  };

  const addPoint = (p: StrokePoint) => {
    if (!current.current.length) {
      current.current = [p];
      live.current = [...live.current, current.current];
    } else {
      const last = current.current[current.current.length - 1];
      if (Math.abs(last.x - p.x) < 0.8 && Math.abs(last.y - p.y) < 0.8) return;
      current.current = [...current.current, p];
      live.current = [...live.current.slice(0, -1), current.current];
    }
    setStrokes(live.current);
  };

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => !disabled,
        onMoveShouldSetPanResponder: () => !disabled,
        onPanResponderTerminationRequest: () => false,
        onShouldBlockNativeResponder: () => true,
        onPanResponderGrant: (e) => {
          if (disabled) return;
          drawing.current = true;
          onDrawingChange?.(true);
          current.current = [];
          const { locationX, locationY } = e.nativeEvent;
          addPoint({ x: locationX, y: locationY });
        },
        onPanResponderMove: (e) => {
          if (!drawing.current || disabled) return;
          const { locationX, locationY } = e.nativeEvent;
          addPoint({ x: locationX, y: locationY });
        },
        onPanResponderRelease: () => {
          drawing.current = false;
          onDrawingChange?.(false);
          emit(live.current);
        },
        onPanResponderTerminate: () => {
          drawing.current = false;
          onDrawingChange?.(false);
          emit(live.current);
        },
      }),
    [disabled, size.w, size.h]
  );

  const clear = () => {
    live.current = [];
    current.current = [];
    setStrokes([]);
    onChange(null);
  };

  const empty = !hasDrawableStrokes(strokes);

  return (
    <View testID={testID}>
      <View
        testID={`${testID}-canvas`}
        style={[styles.pad, { height }, disabled && { opacity: 0.55 }]}
        onLayout={(e) => {
          const { width, height: h } = e.nativeEvent.layout;
          setSize({ w: width, h });
        }}
        {...responder.panHandlers}
      >
        {strokes.map((stroke, si) =>
          stroke.slice(1).map((pt, i) => {
            const prev = stroke[i];
            const dx = pt.x - prev.x;
            const dy = pt.y - prev.y;
            const len = Math.max(1, Math.hypot(dx, dy));
            const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
            return (
              <View
                key={`${si}-${i}`}
                pointerEvents="none"
                style={[
                  styles.seg,
                  {
                    width: len,
                    left: (prev.x + pt.x) / 2 - len / 2,
                    top: (prev.y + pt.y) / 2 - 1.4,
                    transform: [{ rotate: `${angle}deg` }],
                  },
                ]}
              />
            );
          })
        )}
        {empty ? (
          <Text pointerEvents="none" style={styles.hint}>
            Tanda tangan di sini
          </Text>
        ) : null}
      </View>
      {showClear ? (
        <View style={styles.actions}>
          <GhostButton
            title="Hapus / Ulangi"
            onPress={clear}
            disabled={disabled || empty}
            testID={`${testID}-clear`}
            style={{ minWidth: 140 }}
          />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  pad: {
    width: "100%",
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: "#C4B89A",
    borderRadius: 10,
    backgroundColor: "#FDFAF3",
    overflow: "hidden",
  },
  hint: {
    ...typography.subtitle,
    position: "absolute",
    alignSelf: "center",
    top: "42%",
    color: "#A19881",
    fontSize: 13,
  },
  seg: {
    position: "absolute",
    height: 2.8,
    backgroundColor: "#111",
    borderRadius: 2,
  },
  actions: {
    marginTop: space.xs,
    alignItems: "flex-end",
  },
});
