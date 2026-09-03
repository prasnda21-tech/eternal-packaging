import * as React from "react"

import { cn } from "@/lib/utils"

const Textarea = React.forwardRef(({ className, ...props }, ref) => {
  return (
    <textarea
      className={cn(
        "ep-input flex min-h-[88px] w-full rounded-xl border-2 px-3 py-2 text-base placeholder:text-slate-400 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm read-only:bg-slate-100 read-only:border-slate-200 read-only:text-slate-600",
        className
      )}
      ref={ref}
      {...props} />
  );
})
Textarea.displayName = "Textarea"

export { Textarea }
