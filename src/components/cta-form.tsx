import { useState, type FormEvent } from "react";
import { ArrowUpRight, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export function CtaForm() {
  const [submitted, setSubmitted] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitted(true);
  }

  return (
    <div id="access" className="scroll-mt-24">
      <h2 className="font-display text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
        Start building
      </h2>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        Tell us what you want to build. We'll send early access and drop
        your first page straight into the pipeline.
      </p>

      {submitted ? (
        <div className="mt-8 flex items-center gap-3 border border-border bg-card/60 px-5 py-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ember text-primary-foreground">
            <Check size={16} strokeWidth={3} />
          </span>
          <p className="text-sm text-foreground">
            You're on the list — we'll email your access details shortly.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="mt-8 max-w-md space-y-6">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input id="name" name="name" placeholder="Jordan Blake" required />
          </div>

          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              name="email"
              type="email"
              placeholder="jordan@studio.com"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="brief">What do you want to build?</Label>
            <Textarea
              id="brief"
              name="brief"
              placeholder="A waitlist page for a coffee subscription launching in Detroit..."
              required
            />
          </div>

          <Button type="submit" size="lg" className="group w-full sm:w-auto">
            Get early access
            <ArrowUpRight
              size={18}
              className="transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
            />
          </Button>
        </form>
      )}
    </div>
  );
}
