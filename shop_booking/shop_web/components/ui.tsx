import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

/* ---------------------------------------------------------------- Card
 * `.wf` của wireframe: khung trắng viền đậm 1.5px, bo nhẹ 6px, đổ bóng lệch
 * kiểu bản vẽ (2px 3px, không blur). */

export function Card({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "overflow-hidden rounded-md border-[1.5px] border-frame bg-surface shadow-[2px_3px_0_rgba(0,0,0,0.08)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Thanh tiêu đề cửa sổ `.wt`: hai chấm tròn + tiêu đề + phần tử bên phải. */
export function WindowBar({
  title,
  children,
  className,
}: {
  title: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex flex-wrap items-center gap-2 border-b-[1.5px] border-frame bg-surface-2 px-3 py-1.5 text-xs text-ink-2",
        className,
      )}
    >
      <span aria-hidden className="size-[9px] rounded-full border-[1.2px] border-ink-3" />
      <span aria-hidden className="size-[9px] rounded-full border-[1.2px] border-ink-3" />
      <b className="text-ink">{title}</b>
      <span className="flex-1" />
      {children}
    </div>
  );
}

/** Một hàng "nhãn bên trái — nội dung bên phải", như .sec/.sl của wireframe. */
export function Field({
  label,
  hint,
  children,
  last,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  last?: boolean;
}) {
  return (
    <div
      className={cx(
        "flex flex-col gap-2 px-4 py-3 sm:flex-row sm:gap-4",
        !last && "border-b border-dashed border-line",
      )}
    >
      <div className="sm:w-28 sm:shrink-0 sm:pt-1">
        <div className="text-sm text-ink-3">{label}</div>
        {hint ? <div className="mt-0.5 text-xs text-ink-3">{hint}</div> : null}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/* -------------------------------------------------------------- Button
 * `.box` của wireframe: viền mảnh, bo 4px, chữ nhỏ.
 *  - primary  = .box.b (nền tối #333, chữ trắng)
 *  - outline  = .box   (viền xám)
 *  - accent   = .box.a (viền + chữ xanh)
 *  - ghost    = không viền */

type ButtonVariant = "primary" | "outline" | "accent" | "ghost";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
};

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded border px-3 py-1.5 text-sm transition-colors disabled:pointer-events-none disabled:opacity-45";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "border-sel bg-sel text-white hover:border-ink hover:bg-ink",
  outline:
    "border-line-strong bg-surface text-ink hover:bg-surface-2",
  accent:
    "border-accent bg-surface text-accent hover:bg-accent-soft hover:text-accent-hover",
  ghost: "border-transparent text-ink-2 hover:bg-surface-2 hover:text-ink",
};

/** Cho <Link> trông như Button — lồng <Link> trong <button> là HTML sai. */
export function buttonClass(
  variant: ButtonVariant = "outline",
  className?: string,
) {
  return cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className);
}

export function Button({
  variant = "outline",
  className,
  ...props
}: ButtonProps) {
  return <button className={buttonClass(variant, className)} {...props} />;
}

/* ---------------------------------------------------------------- Chip
 * `.chip` của wireframe: viền xám bo 3px chữ nhỏ.
 *  - selected + tone "dark"   = .chip.on (nền tối)
 *  - selected + tone "accent" = .chip.ac (nền xanh nhạt) */

export function Chip({
  selected,
  disabled,
  tone = "dark",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  selected?: boolean;
  tone?: "dark" | "accent";
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      className={cx(
        "rounded-[3px] border px-2 py-0.5 text-xs transition-colors",
        selected
          ? tone === "dark"
            ? "border-sel bg-sel text-white"
            : "border-accent bg-accent-soft text-accent-hover"
          : "border-line-strong bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink",
        disabled &&
          "cursor-not-allowed border-dashed border-line bg-surface text-ink-3 opacity-60 hover:bg-surface",
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------------------------------------- Note
 * `.note`: mẩu ghi chú nền vàng của wireframe. */

export function Note({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-block rounded-[3px] border border-warn-line bg-warn-soft px-2 py-0.5 text-xs text-warn",
        className,
      )}
    >
      {children}
    </span>
  );
}

/* --------------------------------------------------------------- Alert */

const ALERT_TONES = {
  danger: "border-danger-line bg-danger-soft text-danger",
  warn: "border-warn-line bg-warn-soft text-warn",
  info: "border-accent-line bg-accent-soft text-accent-hover",
  success: "border-success-line bg-success-soft text-success",
} as const;

export function Alert({
  tone = "danger",
  title,
  children,
  className,
}: {
  tone?: keyof typeof ALERT_TONES;
  title?: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cx(
        "rounded border-[1.2px] px-3 py-2 text-sm",
        ALERT_TONES[tone],
        className,
      )}
    >
      {title ? <div className="font-medium">{title}</div> : null}
      {children ? (
        <div className={cx("leading-relaxed", title && "mt-1")}>{children}</div>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------- TextInput */

export function TextInput({
  className,
  invalid,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      className={cx(
        "w-full rounded border bg-surface px-2.5 py-1.5 text-sm text-ink transition-colors placeholder:text-ink-3",
        invalid ? "border-danger" : "border-line-strong hover:border-ink-3",
        "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25",
        className,
      )}
      {...props}
    />
  );
}

/* ------------------------------------------------------------- Spinner */

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cx(
        "inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}

export function LoadingLine({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 py-2 text-sm text-ink-2">
      <Spinner />
      {label}
    </div>
  );
}
