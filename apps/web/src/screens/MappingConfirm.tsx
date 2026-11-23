import { useEffect, useState } from "react";
import type { TableMapping } from "@c2b/ts-types";
import { Link, useNavigate, useParams } from "react-router";
import { Qa, WhatHappens } from "../components/Help";
import { ApiError, confirmMapping, getMapping } from "../lib/api";
import { loadToken } from "../lib/storage";

const TARGET_FIELDS = [
  { value: "part_number", label: "Part Number" },
  { value: "description", label: "Description" },
  { value: "quantity", label: "Quantity" },
  { value: "uom", label: "Unit of Measure (UoM)" },
  { value: "find_no", label: "Find Number / Item No" },
  { value: "refdes", label: "Reference Designators" },
  { value: "manufacturer", label: "Manufacturer" },
  { value: "mpn", label: "Manufacturer Part Number (MPN)" },
  { value: "material", label: "Material" },
  { value: "finish", label: "Finish / Surface" },
  { value: "mass", label: "Mass / Weight" },
  { value: "level", label: "Hierarchy Level" },
  { value: "revision", label: "Revision" },
  { value: "value", label: "Component value (e.g. 10k, 100nF)" },
  { value: "footprint", label: "Footprint / Package" },
  { value: "make_buy", label: "Make or buy" },
  { value: "notes", label: "Notes / Remarks" },
  { value: "ignore", label: "Ignore this column" },
];

export function MappingConfirm() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mapping, setMapping] = useState<TableMapping | null>(null);
  const [columnChoices, setColumnChoices] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!id) return;
    const token = loadToken(id) ?? "";
    getMapping(id, token)
      .then((data) => {
        setMapping(data);
        const initialChoices: Record<string, string> = {};
        for (const [col, info] of Object.entries(data.columns ?? {})) {
          initialChoices[col] = info.field;
        }
        setColumnChoices(initialChoices);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "The mapping could not be loaded.");
        setLoading(false);
      });
  }, [id]);

  const handleFieldChange = (col: string, field: string) => {
    setColumnChoices((prev) => ({ ...prev, [col]: field }));
  };

  const handleConfirm = async () => {
    if (!id) return;
    setSaving(true);
    const token = loadToken(id) ?? "";
    try {
      await confirmMapping(id, token, columnChoices);
      void navigate(`/j/${id}`);
    } catch (err: unknown) {
      setError(
        err instanceof ApiError || err instanceof Error
          ? err.message
          : "The mapping could not be saved.",
      );
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="stack">
        <h1>Detecting BoM columns...</h1>
        <p className="muted">Analyzing table boundaries and matching column headers against the rules library.</p>
      </div>
    );
  }

  if (error || !mapping) {
    return (
      <div className="stack">
        <h1>Header Mapping</h1>
        <div className="card notice-error" role="alert">
          <p>{error || "No mapping available"}</p>
        </div>
        <p>
          <Link to={`/j/${id || ""}`} className="button secondary">
            Back to Upload
          </Link>
        </p>
      </div>
    );
  }

  const columns = mapping.columns ?? {};
  const cols = Object.keys(columns);
  const ambiguous = new Set<string>(mapping.ambiguous_columns ?? []);

  return (
    <div className="stack">
      <div className="flow">
        <h1>Confirm BoM File Mapping</h1>
        <p className="lead">
          We detected your table starting at <strong>Row {mapping.header_row}</strong>. Check how each column was
          mapped to BoM fields, adjust any ambiguous columns, and confirm.
        </p>
      </div>

      <WhatHappens>
        <p>
          Columns are scored using deterministic regex rules from the library and pattern-matching on cell values.
          Ambiguous columns (e.g. headers like 'Item' that could mean find number or description) are highlighted for
          your review. Your raw values are always kept verbatim and never rewritten.
        </p>
      </WhatHappens>

      {ambiguous.size > 0 && (
        <div className="card notice-warning" role="status">
          <strong>Review required for ambiguous columns:</strong>
          <p className="small">
            The columns [{Array.from(ambiguous).join(", ")}] have multiple competing field interpretations. Please
            confirm the field assignment below.
          </p>
        </div>
      )}

      <div className="card">
        <h2>Column Field Assignments</h2>
        <div className="mapping-grid" style={{ display: "grid", gap: "1rem", marginTop: "1rem" }}>
          {cols.map((colName) => {
            const isAmbiguous = ambiguous.has(colName);
            const currentField = columnChoices[colName] ?? columns[colName]?.field;
            return (
              <div
                key={colName}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0.75rem",
                  border: isAmbiguous ? "2px solid var(--warning, #e67e22)" : "1px solid var(--border)",
                  borderRadius: "var(--radius, 6px)",
                  background: isAmbiguous ? "var(--warning-soft, rgba(230,126,34,0.08))" : "transparent",
                }}
              >
                <div>
                  <strong>{colName}</strong>
                  {isAmbiguous && (
                    <span
                      style={{
                        marginLeft: "0.5rem",
                        fontSize: "0.75rem",
                        padding: "0.1rem 0.4rem",
                        background: "var(--warning, #e67e22)",
                        color: "#fff",
                        borderRadius: "3px",
                      }}
                    >
                      Ambiguous
                    </span>
                  )}
                  {columns[colName]?.why && (
                    <p className="small muted" style={{ margin: "0.25rem 0 0" }}>
                      {columns[colName].why}
                    </p>
                  )}
                </div>
                <div>
                  <select
                    aria-label={`Field mapping for column ${colName}`}
                    value={currentField}
                    onChange={(e) => {
                      handleFieldChange(colName, e.target.value);
                    }}
                    style={{ padding: "0.4rem 0.75rem", borderRadius: "4px" }}
                  >
                    {TARGET_FIELDS.map((tf) => (
                      <option key={tf.value} value={tf.value}>
                        {tf.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ display: "flex", gap: "1rem", marginTop: "1rem" }}>
        <button
          type="button"
          className="button primary"
          disabled={saving}
          onClick={() => {
            void handleConfirm();
          }}
        >
          {saving ? "Confirming..." : "Confirm Mapping & Continue"}
        </button>
        <Link to={`/j/${id || ""}`} className="button secondary">
          Cancel
        </Link>
      </div>

      <div style={{ marginTop: "2rem" }}>
        <Qa
          items={[
            {
              q: "What if my spreadsheet has a title block or notes at the bottom?",
              a: (
                <p>
                  Rows before the header row (such as company headers or author information) are automatically classified
                  as title block rows. Rows at the bottom (like summary totals or terms) are flagged as footer rows and
                  excluded from parts extraction.
                </p>
              ),
            },
            {
              q: "Can I change this mapping later?",
              a: (
                <p>
                  Yes. You can edit any value in the review table, re-map columns, or re-run the intake process at any point.
                </p>
              ),
            },
          ]}
        />
      </div>
    </div>
  );
}
