import { useEffect, useState } from "react";
import api from "../lib/api";

export default function PrintTemplateMenu({ documentType, onSelect, testId = "print-template-menu" }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    let active = true;
    api.listActivePrintTemplates(documentType)
      .then((res) => { if (active) setItems(res.templates || []); })
      .catch(() => { if (active) setItems([]); });
    return () => { active = false; };
  }, [documentType]);
  if (items.length < 2) return null;
  return (
    <details className="pt-print-menu" data-testid={testId}>
      <summary>Pilih Cetakan</summary>
      <div className="pt-print-pop">
        {items.map((item) => (
          <button type="button" key={item.id} onClick={() => onSelect(item.id)}>
            {item.name} · {item.audience_label}{item.is_default ? " · Default" : ""}
          </button>
        ))}
      </div>
    </details>
  );
}
