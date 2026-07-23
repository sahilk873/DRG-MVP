import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

// Node 26 + jsdom 29 no longer expose window.localStorage by default (Node's
// built-in requires --localstorage-file). Provide a minimal, spec-shaped in-memory
// Storage so the reviewer-workflow tests can exercise real persistence logic.
if (typeof window !== 'undefined' && !('localStorage' in window && window.localStorage)) {
  const createMemoryStorage = (): Storage => {
    const store = new Map<string, string>()
    return {
      get length() { return store.size },
      clear: () => store.clear(),
      getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      removeItem: (key: string) => { store.delete(key) },
      setItem: (key: string, value: string) => { store.set(key, String(value)) },
    } as Storage
  }
  Object.defineProperty(window, 'localStorage', { value: createMemoryStorage(), writable: true })
}

Object.defineProperty(window, 'scrollTo', { value: () => undefined, writable: true })
Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:synthetic-export', writable: true })
Object.defineProperty(URL, 'revokeObjectURL', { value: () => undefined, writable: true })
