/** Isolated display-row proof data. Not wired into the live schedule page. */

export function fauzanPlastikPe28ProofRow() {
  return {
    id: "po-fauzan-pe",
    po_number: "PO-FAUZAN-PE",
    customer: "Customer PE",
    work_date: "2026-09-16",
    current_stage: 8,
    status_final: "Operator",
    total_quantity: 4600,
    print_qty_total: 4600,
    operators: [
      {
        split_id: "s-fauzan-a",
        operator_user_id: "u-fauzan",
        operator_name: "Fauzan",
        line_name: "Plastik PE 28",
        line_id: "lid-pe-a",
        line_idx: 0,
        line_printable: true,
        quantity: 2000,
        work_date: "2026-09-16",
      },
      {
        split_id: "s-fauzan-b",
        operator_user_id: "u-fauzan",
        operator_name: "Fauzan",
        line_name: "Plastik PE 28",
        line_id: "lid-pe-b",
        line_idx: 1,
        line_printable: true,
        quantity: 2600,
        work_date: "2026-09-16",
      },
    ],
  };
}

export function singleItemLineProofRow() {
  return {
    id: "po-one-line",
    po_number: "PO-ONE-LINE",
    customer: "Customer One",
    operators: [
      {
        split_id: "s-one",
        operator_user_id: "u-budi",
        operator_name: "Budi",
        line_name: "Cup 16oz",
        line_id: "lid-cup",
        line_idx: 0,
        line_printable: true,
        quantity: 5000,
      },
    ],
  };
}

export function isolatedOperatorsProofRow() {
  return {
    id: "po-two-ops",
    po_number: "PO-TWO-OPS",
    customer: "Customer Two",
    operators: [
      {
        split_id: "s-budi",
        operator_user_id: "u-budi",
        operator_name: "Budi",
        line_name: "Cup 16oz",
        line_id: "lid-a",
        line_idx: 0,
        line_printable: true,
        quantity: 1000,
      },
      {
        split_id: "s-andi",
        operator_user_id: "u-andi",
        operator_name: "Andi",
        line_name: "Cup 16oz",
        line_id: "lid-a",
        line_idx: 0,
        line_printable: true,
        quantity: 7000,
      },
    ],
  };
}

export function unassignedProofRow() {
  return {
    id: "po-unassigned",
    po_number: "PO-UNASSIGNED",
    customer: "Customer Open",
    operators: [],
  };
}
