import { useStore } from "../store";

export type Mood = "neutral" | "happy" | "thinking" | "sleepy" | "surprised";

// Shows the mood still when one exists, otherwise the nautilus mark.
export function Avatar({ mood = "neutral", size = 32 }: { mood?: Mood; size?: number }) {
  const { state } = useStore();
  const faces = state?.avatar.faces ?? {};
  const src = faces[mood] ?? faces.neutral;
  return (
    <img
      src={src ?? "/nemo.svg"}
      alt=""
      width={size}
      height={size}
      className={`shrink-0 rounded-full object-cover ${mood === "thinking" && !src ? "animate-pulse" : ""}`}
      style={{ width: size, height: size }}
    />
  );
}
