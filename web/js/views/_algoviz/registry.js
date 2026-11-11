// Rejestr wszystkich wizualizacji (czysty JS — importowany też przez testy w node).
import { SORTING } from './sorting.js';
import { SEARCHING } from './searching.js';
import { GRAPHS } from './graphs.js';
import { STRUCTURES } from './structures.js';
import { DP } from './dp.js';
import { MISC } from './misc.js';

export const ALGOS = [...SORTING, ...SEARCHING, ...GRAPHS, ...STRUCTURES, ...DP, ...MISC];

export const CATEGORIES = [
  { id: 'Sortowanie', icon: 'list-checks' },
  { id: 'Wyszukiwanie i techniki', icon: 'search' },
  { id: 'Grafy', icon: 'network' },
  { id: 'Struktury danych', icon: 'list-tree' },
  { id: 'Programowanie dynamiczne', icon: 'table' },
  { id: 'Teksty', icon: 'align-left' },
  { id: 'Matematyka', icon: 'sigma' },
  { id: 'Rekurencja', icon: 'git-branch' },
];

export const byId = new Map(ALGOS.map((a) => [a.id, a]));

/** Wartości domyślne pól wejściowych algorytmu. */
export function defaultsFor(algo) {
  const vals = {};
  for (const inp of algo.inputs || []) vals[inp.key] = inp.def;
  return { ...vals, ...(algo.defaults ? algo.defaults() : {}) };
}

export function codeFor(algo, vals) {
  return typeof algo.code === 'function' ? algo.code(vals) : algo.code;
}
