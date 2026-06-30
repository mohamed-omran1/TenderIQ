"use client";

/**
 * CompanyProfileForm — editable company benchmarking profile (REQ-002 Slice 3).
 *
 * - Fetches the profile on mount via TanStack Query v5 useQuery.
 * - Renders a skeleton loader while fetching.
 * - Shows an onboarding banner when the profile has never been filled.
 * - Validates client-side with Zod before calling PUT /company-profile.
 * - Surfaces 422 field-level errors under each field and generic errors in a
 *   top-level banner.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { toast, Toaster } from "sonner";
import { AlertCircle, Loader2, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import {
  ApiError,
  ValidationError,
  getCompanyProfile,
  updateCompanyProfile,
  type CompanyProfileApiResponse,
  type CompanyProfileSchema,
  type FieldError,
  type FinancialCapacity,
  type PastProject,
} from "@/lib/api/company";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SPECIALIZATION_OPTIONS = [
  { value: "civil", label: "Civil" },
  { value: "mep", label: "MEP" },
  { value: "fit-out", label: "Fit-out" },
  { value: "roads", label: "Roads" },
  { value: "water", label: "Water" },
] as const;

type SpecializationValue = (typeof SPECIALIZATION_OPTIONS)[number]["value"];

const COUNTRY_OPTIONS = [
  { value: "EG", label: "Egypt" },
  { value: "SA", label: "Saudi Arabia" },
  { value: "AE", label: "United Arab Emirates" },
  { value: "QA", label: "Qatar" },
  { value: "KW", label: "Kuwait" },
  { value: "BH", label: "Bahrain" },
  { value: "OM", label: "Oman" },
] as const;

type CountryValue = (typeof COUNTRY_OPTIONS)[number]["value"];

const MAX_PROJECTS = 20;

// ---------------------------------------------------------------------------
// Zod schema — mirrors backend/app/schemas/company.py validation rules.
// ---------------------------------------------------------------------------

const pastProjectSchema = z.object({
  name: z.string().min(1, "Project name is required").max(512),
  value: z.number({ invalid_type_error: "Value must be a number" }).positive(),
  year: z
    .number({ invalid_type_error: "Year must be a number" })
    .int()
    .min(1900)
    .max(2100),
  sector: z.string().min(1, "Sector is required").max(128),
});

const financialCapacitySchema = z.object({
  currency: z
    .string()
    .length(3, "Currency must be 3 letters")
    .regex(/^[A-Za-z]{3}$/, "Invalid ISO 4217 currency code"),
  annual_turnover: z
    .number({ invalid_type_error: "Annual turnover must be a number" })
    .positive("Annual turnover must be positive"),
  available_bonding_capacity: z
    .number({
      invalid_type_error: "Available bonding capacity must be a number",
    })
    .min(0, "Available bonding capacity must be >= 0"),
});

const companyProfileFormSchema = z.object({
  specializations: z
    .array(z.enum(["civil", "mep", "fit-out", "roads", "water"]))
    .min(1, "At least one specialisation is required"),
  financial_capacity: financialCapacitySchema,
  geographic_reach: z
    .array(
      z
        .string()
        .length(2, "Country code must be 2 letters")
        .regex(/^[A-Z]{2}$/, "Invalid ISO 3166-1 alpha-2 country code"),
    )
    .min(1, "At least one country is required"),
  past_projects: z
    .array(pastProjectSchema)
    .max(MAX_PROJECTS, `Maximum ${MAX_PROJECTS} projects allowed`),
  max_project_value: z
    .number({
      invalid_type_error: "Maximum project value must be a number",
    })
    .positive("max_project_value must be a positive number"),
});

type CompanyProfileFormValues = z.infer<typeof companyProfileFormSchema>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultValues: CompanyProfileFormValues = {
  specializations: [],
  financial_capacity: {
    currency: "",
    annual_turnover: 0,
    available_bonding_capacity: 0,
  },
  geographic_reach: [],
  past_projects: [],
  max_project_value: 0,
};

function isEmptyProfile(data: CompanyProfileFormValues): boolean {
  return (
    data.specializations.length === 0 &&
    data.financial_capacity.currency === "" &&
    data.financial_capacity.annual_turnover === 0 &&
    data.financial_capacity.available_bonding_capacity === 0 &&
    data.geographic_reach.length === 0 &&
    data.past_projects.length === 0 &&
    data.max_project_value === 0
  );
}

function normalizeApiResponse(
  data: CompanyProfileApiResponse,
): CompanyProfileFormValues {
  const rawSpecializations = data.specializations ?? [];
  const rawFinancial: FinancialCapacity | null = data.financial_capacity ?? null;
  const rawReach = data.geographic_reach ?? [];
  const rawProjects = data.past_projects ?? [];

  const specializations = rawSpecializations
    .map((s) => s.toLowerCase())
    .filter((s): s is SpecializationValue =>
      SPECIALIZATION_OPTIONS.some((o) => o.value === s),
    );

  const financialCapacity: FinancialCapacity = rawFinancial ?? {
    currency: "",
    annual_turnover: 0,
    available_bonding_capacity: 0,
  };

  return {
    specializations,
    financial_capacity: {
      currency: financialCapacity.currency ?? "",
      annual_turnover: financialCapacity.annual_turnover ?? 0,
      available_bonding_capacity:
        financialCapacity.available_bonding_capacity ?? 0,
    },
    geographic_reach: rawReach
      .map((c) => c.toUpperCase())
      .filter((c): c is CountryValue =>
        COUNTRY_OPTIONS.some((o) => o.value === c),
      ),
    past_projects: rawProjects.map((p) => ({
      name: p.name,
      value: p.value,
      year: p.year,
      sector: p.sector,
    })),
    max_project_value: data.max_project_value ?? 0,
  };
}

function mapValidationDetails(
  details: FieldError[],
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const detail of details) {
    // FastAPI detail.loc starts with "body", e.g. ["body", "specializations"].
    const path = detail.loc.slice(1);
    const key = path.join(".");
    const existing = errors[key];
    errors[key] = existing ? `${existing}; ${detail.msg}` : detail.msg;
  }
  return errors;
}

function formatNumberInput(value: number): string | number {
  return value === 0 ? "" : value;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CompanyProfileForm() {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState<CompanyProfileFormValues>(
    defaultValues,
  );
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [topLevelError, setTopLevelError] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["company-profile"],
    queryFn: getCompanyProfile,
  });

  useEffect(() => {
    if (data) {
      setFormData(normalizeApiResponse(data));
    }
  }, [data]);

  const mutation = useMutation({
    mutationFn: updateCompanyProfile,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["company-profile"] });
      toast.success("Profile saved successfully");
      setClientErrors({});
      setServerErrors({});
      setTopLevelError(null);
    },
    onError: (err) => {
      if (err instanceof ValidationError) {
        setServerErrors(mapValidationDetails(err.details));
        setTopLevelError(null);
      } else if (err instanceof ApiError) {
        setTopLevelError(
          `Server returned HTTP ${err.status}. Please try again.`,
        );
      } else {
        setTopLevelError("An unexpected error occurred. Please try again.");
      }
    },
  });

  const isOnboarding = useMemo(
    () => isEmptyProfile(formData),
    [formData],
  );

  function setField<K extends keyof CompanyProfileFormValues>(
    field: K,
    value: CompanyProfileFormValues[K],
  ) {
    setFormData((prev) => ({ ...prev, [field]: value }) as CompanyProfileFormValues);
  }

  function setFinancialField<
    K extends keyof CompanyProfileFormValues["financial_capacity"],
  >(field: K, value: CompanyProfileFormValues["financial_capacity"][K]) {
    setFormData(
      (prev) =>
        ({
          ...prev,
          financial_capacity: { ...prev.financial_capacity, [field]: value },
        }) as CompanyProfileFormValues,
    );
  }

  function toggleSpecialization(value: SpecializationValue) {
    setFormData((prev) => {
      const next = new Set(prev.specializations);
      if (next.has(value)) {
        next.delete(value);
      } else {
        next.add(value);
      }
      return {
        ...prev,
        specializations: Array.from(next) as SpecializationValue[],
      };
    });
  }

  function toggleCountry(value: CountryValue) {
    setFormData((prev) => {
      const next = new Set(prev.geographic_reach);
      if (next.has(value)) {
        next.delete(value);
      } else {
        next.add(value);
      }
      return { ...prev, geographic_reach: Array.from(next) as CountryValue[] };
    });
  }

  function addPastProject() {
    setFormData((prev) => ({
      ...prev,
      past_projects: [
        ...prev.past_projects,
        {
          name: "",
          value: 0,
          year: new Date().getFullYear(),
          sector: "",
        } satisfies PastProject,
      ],
    }));
  }

  function removePastProject(index: number) {
    setFormData((prev) => ({
      ...prev,
      past_projects: prev.past_projects.filter((_, i) => i !== index),
    }));
  }

  function updatePastProject(index: number, updates: Partial<PastProject>) {
    setFormData((prev) => ({
      ...prev,
      past_projects: prev.past_projects.map((project, i) =>
        i === index ? { ...project, ...updates } : project,
      ),
    }));
  }

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setClientErrors({});
    setServerErrors({});
    setTopLevelError(null);

    const result = companyProfileFormSchema.safeParse(formData);
    if (!result.success) {
      const errors: Record<string, string> = {};
      for (const issue of result.error.issues) {
        const key = issue.path.join(".");
        errors[key] = issue.message;
      }
      setClientErrors(errors);
      return;
    }

    mutation.mutate(result.data);
  }

  function combinedError(key: string): string | undefined {
    return clientErrors[key] ?? serverErrors[key];
  }

  if (isLoading) {
    return <ProfileSkeleton />;
  }

  if (isError) {
    const message =
      error instanceof ApiError
        ? `Failed to load profile (HTTP ${error.status}).`
        : "Failed to load profile. Please try again.";
    return (
      <div className="flex items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
        <AlertCircle className="size-4" />
        {message}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Toaster position="top-right" richColors />

      {isOnboarding && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          Complete your company profile to enable tender analysis.
        </div>
      )}

      {topLevelError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
          <AlertCircle className="size-4" />
          {topLevelError}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-8">
        <Card>
          <CardContent className="space-y-6 pt-6">
            {/* Specializations */}
            <div className="space-y-2">
              <Label>Specializations</Label>
              <div className="flex flex-wrap gap-2">
                {SPECIALIZATION_OPTIONS.map((option) => {
                  const selected = formData.specializations.includes(
                    option.value,
                  );
                  return (
                    <Button
                      key={option.value}
                      type="button"
                      variant={selected ? "default" : "outline"}
                      size="sm"
                      onClick={() => toggleSpecialization(option.value)}
                      aria-pressed={selected}
                    >
                      {option.label}
                    </Button>
                  );
                })}
              </div>
              {combinedError("specializations") && (
                <p className="text-sm text-destructive">
                  {combinedError("specializations")}
                </p>
              )}
            </div>

            {/* Financial Capacity */}
            <div className="space-y-4">
              <Label>Financial Capacity</Label>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label
                    htmlFor="currency"
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Currency (ISO 4217)
                  </Label>
                  <Input
                    id="currency"
                    value={formData.financial_capacity.currency}
                    onChange={(e) =>
                      setFinancialField(
                        "currency",
                        e.target.value.toUpperCase(),
                      )
                    }
                    placeholder="USD"
                    maxLength={3}
                  />
                  {combinedError("financial_capacity.currency") && (
                    <p className="text-sm text-destructive">
                      {combinedError("financial_capacity.currency")}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label
                    htmlFor="annual_turnover"
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Annual Turnover
                  </Label>
                  <Input
                    id="annual_turnover"
                    type="number"
                    value={formatNumberInput(
                      formData.financial_capacity.annual_turnover,
                    )}
                    onChange={(e) =>
                      setFinancialField(
                        "annual_turnover",
                        e.target.value === "" ? 0 : Number(e.target.value),
                      )
                    }
                    placeholder="0"
                    min={0}
                    step="any"
                  />
                  {combinedError("financial_capacity.annual_turnover") && (
                    <p className="text-sm text-destructive">
                      {combinedError("financial_capacity.annual_turnover")}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label
                    htmlFor="available_bonding_capacity"
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Available Bonding Capacity
                  </Label>
                  <Input
                    id="available_bonding_capacity"
                    type="number"
                    value={formatNumberInput(
                      formData.financial_capacity.available_bonding_capacity,
                    )}
                    onChange={(e) =>
                      setFinancialField(
                        "available_bonding_capacity",
                        e.target.value === "" ? 0 : Number(e.target.value),
                      )
                    }
                    placeholder="0"
                    min={0}
                    step="any"
                  />
                  {combinedError(
                    "financial_capacity.available_bonding_capacity",
                  ) && (
                    <p className="text-sm text-destructive">
                      {combinedError(
                        "financial_capacity.available_bonding_capacity",
                      )}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* Geographic Reach */}
            <div className="space-y-2">
              <Label>Geographic Reach</Label>
              <div className="flex flex-wrap gap-2">
                {COUNTRY_OPTIONS.map((option) => {
                  const selected = formData.geographic_reach.includes(
                    option.value,
                  );
                  return (
                    <Button
                      key={option.value}
                      type="button"
                      variant={selected ? "default" : "outline"}
                      size="sm"
                      onClick={() => toggleCountry(option.value)}
                      aria-pressed={selected}
                    >
                      {option.label}
                    </Button>
                  );
                })}
              </div>
              {combinedError("geographic_reach") && (
                <p className="text-sm text-destructive">
                  {combinedError("geographic_reach")}
                </p>
              )}
            </div>

            {/* Max Project Value */}
            <div className="space-y-2">
              <Label htmlFor="max_project_value">Maximum Project Value</Label>
              <Input
                id="max_project_value"
                type="number"
                value={formatNumberInput(formData.max_project_value)}
                onChange={(e) =>
                  setField(
                    "max_project_value",
                    e.target.value === "" ? 0 : Number(e.target.value),
                  )
                }
                placeholder="0"
                min={0}
                step="any"
              />
              {combinedError("max_project_value") && (
                <p className="text-sm text-destructive">
                  {combinedError("max_project_value")}
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Past Projects */}
        <Card>
          <CardContent className="space-y-4 pt-6">
            <div className="flex items-center justify-between">
              <Label>Past Projects</Label>
              <span className="text-xs text-muted-foreground">
                {formData.past_projects.length} / {MAX_PROJECTS} projects
              </span>
            </div>

            {formData.past_projects.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No past projects added.
              </p>
            )}

            {formData.past_projects.map((project, index) => (
              <div
                key={index}
                className="grid gap-4 rounded-lg border p-4 sm:grid-cols-[1fr_1fr_120px_1fr_auto]"
              >
                <div className="space-y-2">
                  <Label
                    htmlFor={`project-${index}-name`}
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Name
                  </Label>
                  <Input
                    id={`project-${index}-name`}
                    value={project.name}
                    onChange={(e) =>
                      updatePastProject(index, { name: e.target.value })
                    }
                  />
                  {combinedError(`past_projects.${index}.name`) && (
                    <p className="text-sm text-destructive">
                      {combinedError(`past_projects.${index}.name`)}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label
                    htmlFor={`project-${index}-value`}
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Value
                  </Label>
                  <Input
                    id={`project-${index}-value`}
                    type="number"
                    value={formatNumberInput(project.value)}
                    onChange={(e) =>
                      updatePastProject(index, {
                        value:
                          e.target.value === "" ? 0 : Number(e.target.value),
                      })
                    }
                    min={0}
                    step="any"
                  />
                  {combinedError(`past_projects.${index}.value`) && (
                    <p className="text-sm text-destructive">
                      {combinedError(`past_projects.${index}.value`)}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label
                    htmlFor={`project-${index}-year`}
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Year
                  </Label>
                  <Input
                    id={`project-${index}-year`}
                    type="number"
                    value={formatNumberInput(project.year)}
                    onChange={(e) =>
                      updatePastProject(index, {
                        year:
                          e.target.value === "" ? 0 : Number(e.target.value),
                      })
                    }
                    min={1900}
                    max={2100}
                    step={1}
                  />
                  {combinedError(`past_projects.${index}.year`) && (
                    <p className="text-sm text-destructive">
                      {combinedError(`past_projects.${index}.year`)}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label
                    htmlFor={`project-${index}-sector`}
                    className="text-xs font-normal text-muted-foreground"
                  >
                    Sector
                  </Label>
                  <Input
                    id={`project-${index}-sector`}
                    value={project.sector}
                    onChange={(e) =>
                      updatePastProject(index, { sector: e.target.value })
                    }
                  />
                  {combinedError(`past_projects.${index}.sector`) && (
                    <p className="text-sm text-destructive">
                      {combinedError(`past_projects.${index}.sector`)}
                    </p>
                  )}
                </div>

                <div className="flex items-end">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => removePastProject(index)}
                    aria-label={`Remove project ${project.name || index + 1}`}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              </div>
            ))}

            <Button
              type="button"
              variant="outline"
              onClick={addPastProject}
              disabled={formData.past_projects.length >= MAX_PROJECTS}
              className="w-full"
            >
              <Plus className="mr-2 size-4" />
              Add project
            </Button>
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending && (
              <Loader2 className="mr-2 size-4 animate-spin" />
            )}
            {mutation.isPending ? "Saving..." : "Save profile"}
          </Button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------

function ProfileSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-1/3" />
      <Card>
        <CardContent className="space-y-6 pt-6">
          <Skeleton className="h-4 w-1/4" />
          <div className="flex gap-2">
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-8 w-20" />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
          <Skeleton className="h-4 w-1/4" />
          <div className="flex gap-2">
            <Skeleton className="h-8 w-24" />
            <Skeleton className="h-8 w-24" />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
