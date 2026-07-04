import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

/** 合并 Tailwind class 名（与 artoo 一致的 shadcn 约定）。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
