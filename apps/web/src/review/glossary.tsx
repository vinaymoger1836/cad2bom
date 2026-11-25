import { Term } from "../components/Help";

/** Plain-language meaning of every technical term used on the review screen (docs/07 §3). */
export const GLOSSARY = {
  EBOM: "Engineering BoM: the product as designed, in its assembly structure (what is in what).",
  MBOM: "Manufacturing BoM: the same product as it is built and bought. Modelling groups are folded away and purchased assemblies count as one item.",
  BoM: "Bill of materials: the list of every part and material needed to make one product, with quantities.",
  phantom:
    "A grouping that exists only in the CAD model (for example a folder of screws). It is not built or bought, so the manufacturing BoM leaves it out.",
  "find number":
    "The item number printed next to a part on the drawing. It points from the drawing to a line in the list.",
  refdes:
    "Reference designator: the label of a part on a circuit board, such as R12 or C3.",
  designation:
    "The standard name of a catalogue part, such as ISO 4762 M4x12 (a socket head screw). It lets a buyer order the exact part.",
  confidence:
    "How sure the system is about a value. High and Medium are shown in green and amber, Low in red. Anything you type yourself is always 100%.",
  "make / buy":
    "Whether the part is made in-house, made by a supplier to your drawing (supplier made), or bought from a catalogue (buy).",
  "supplier made": "A custom part a supplier produces to your drawing.",
  suggestion:
    "Something the system proposes to add or change. Nothing is added until you accept it.",
  "rule id":
    "The code of the rule that produced a value, for example R-STD-012. It lets you trace why a value is what it is.",
  provenance: "Where a value came from: CAD, your file, a standard table, a rule, the AI, or you.",
  "AI evidence":
    "An AI check that agreed or disagreed with the rules. It is only a hint: the rules decide the class, and you can change it.",
  "section plane":
    "A virtual cut through the model that lets you look inside. Move the slider to slide the cut.",
  isolate: "Hide every part except the selected one.",
  "purchased items":
    "Everything marked buy, added up by part number, so you can see what to order per product and per year.",
  excluded:
    "CAD bodies the rules left out of the BoM, such as reference geometry or hidden parts, with the reason.",
  version:
    "A frozen copy of the BoM made when you approve it. It never changes; later edits start a new draft.",
} as const;

export type GlossaryTerm = keyof typeof GLOSSARY;

/** A word with a tooltip that explains it. */
export function G({ term, children }: { term: GlossaryTerm; children?: string }) {
  return <Term word={children ?? term} meaning={GLOSSARY[term]} />;
}
