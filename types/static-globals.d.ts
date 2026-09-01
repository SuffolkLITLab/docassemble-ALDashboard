interface DocxLabelerPreviewUtils {
  [key: string]: (...args: any[]) => any;
}

interface Window {
  DOCX_LABELER_CONFIG?: Record<string, any>;
  DocxLabelerPreviewUtils?: any;
  PDFLib?: any;
  JSZip?: any;
}

declare const mammoth: Record<string, any>;

// These labelers are plain browser scripts that look elements up by id and
// immediately use element-specific properties (.value, .checked, .files).
// Widening only the three lookup APIs keeps the rest of the DOM surface
// type-checked -- document.createElement, .body, .addEventListener and
// friends still catch typos and bad arguments.
interface Document {
  getElementById(elementId: string): any;
  querySelector(selectors: string): any;
  querySelectorAll(selectors: string): any;
}
