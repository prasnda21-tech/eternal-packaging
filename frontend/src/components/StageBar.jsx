import { STAGES } from "../lib/constants";

export default function StageBar({ stage, onStageClick }) {
  return (
    <div className="stage-bar" data-testid="order-stage-progress" title={STAGES[stage]}>
      {STAGES.map((s, i) => {
        const isFinal = stage >= STAGES.length - 1;
        const cls = i < stage || isFinal ? "done" : i === stage ? "current" : "";
        const clickable = typeof onStageClick === "function";
        return (
          <span
            key={s}
            className={cls + (clickable ? " clickable" : "")}
            title={`${i + 1}. ${s}${clickable ? " · klik untuk lihat detail" : ""}`}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            onClick={clickable ? () => onStageClick(i) : undefined}
            onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onStageClick(i); } } : undefined}
            data-testid={`stage-node-${i}`}
          />
        );
      })}
    </div>
  );
}
