import { useState, useEffect } from 'react';
import { Card } from '@/app/components/ui/card';
import { Button } from '@/app/components/ui/button';
import { Badge } from '@/app/components/ui/badge';
import { Switch } from '@/app/components/ui/switch';
import { Save, RotateCcw, Percent, IndianRupee, Package, Clock, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { BACKEND_BASE } from '@/lib/runtime-config';

interface RuleConfig {
  discount_threshold_percent: number;
  refund_amount_threshold: number;
  high_value_threshold: number;
  bulk_quantity_threshold: number;
  idle_pos_minutes: number;
  rules?: Record<string, { enabled: boolean }>;
}

const DEFAULTS: RuleConfig = {
  discount_threshold_percent: 20,
  refund_amount_threshold: 0,
  high_value_threshold: 2000,
  bulk_quantity_threshold: 10,
  idle_pos_minutes: 30,
};

interface SettingsPanelProps {
  onConfigSaved?: () => void;
}

interface RuleCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  risk: 'High' | 'Medium';
  value: number;
  onChange: (val: number) => void;
  unit: string;
  min?: number;
  step?: number;
  hint?: string;
}

function RuleCard({ icon, title, description, risk, value, onChange, unit, min = 0, step = 1, hint }: RuleCardProps) {
  return (
    <Card className="bg-white border-gray-200 shadow-sm overflow-hidden">
      <div className="flex items-stretch">
        {/* Left color bar */}
        <div className={`w-1 ${risk === 'High' ? 'bg-red-500' : 'bg-amber-500'}`} />

        <div className="flex-1 p-4">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2.5">
              <div className={`p-2 rounded-lg ${risk === 'High' ? 'bg-red-50' : 'bg-amber-50'}`}>
                {icon}
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
                <p className="text-xs text-gray-400 mt-0.5">{description}</p>
              </div>
            </div>
            <Badge className={`text-[10px] ${risk === 'High' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
              {risk} Risk
            </Badge>
          </div>

          {/* Slider + Input */}
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={min}
              max={unit === '%' ? 100 : unit === 'min' ? 120 : unit === 'items' ? 50 : 10000}
              step={step}
              value={value}
              onChange={e => onChange(parseFloat(e.target.value))}
              className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <div className="flex items-center gap-1 bg-gray-50 border border-gray-200 rounded-lg px-2 py-1 min-w-[90px]">
              <input
                type="number"
                min={min}
                step={step}
                value={value}
                onChange={e => onChange(parseFloat(e.target.value) || 0)}
                className="w-14 bg-transparent text-sm font-mono text-gray-800 text-right outline-none"
              />
              <span className="text-xs text-gray-400">{unit}</span>
            </div>
          </div>

          {hint && (
            <p className="text-[11px] text-gray-400 mt-2 italic">{hint}</p>
          )}
        </div>
      </div>
    </Card>
  );
}

export function SettingsPanel({ onConfigSaved }: SettingsPanelProps) {
  const [config, setConfig] = useState<RuleConfig>(DEFAULTS);
  const [savedConfig, setSavedConfig] = useState<RuleConfig>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const hasChanges = JSON.stringify(config) !== JSON.stringify(savedConfig);

  useEffect(() => {
    fetch(`${BACKEND_BASE}/api/config`)
      .then(res => res.json())
      .then(data => {
        setConfig(data);
        setSavedConfig(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch(`${BACKEND_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      if (res.ok) {
        setSavedConfig(config);
        toast.success('Thresholds saved — re-classifying transactions...');
        onConfigSaved?.();
      } else {
        toast.error('Failed to save');
      }
    } catch {
      toast.error('Connection failed');
    }
    setSaving(false);
  };

  const handleReset = () => {
    setConfig(prev => ({ ...DEFAULTS, rules: prev.rules }));
    toast.info('Reset to defaults (save to apply)');
  };

  if (loading) {
    return <div className="flex items-center justify-center py-16 text-gray-400">Loading configuration...</div>;
  }

  return (
    <div className="space-y-5 max-w-3xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-800">Fraud Detection Rules</h2>
          <p className="text-sm text-gray-500">Adjust thresholds to control when transactions get flagged</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5 text-xs border-gray-200 h-8" onClick={handleReset}>
            <RotateCcw className="h-3 w-3" /> Reset Defaults
          </Button>
          <Button
            size="sm"
            className={`gap-1.5 text-xs h-8 ${hasChanges ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-gray-100 text-gray-400 cursor-not-allowed'}`}
            disabled={!hasChanges || saving}
            onClick={handleSave}
          >
            <Save className="h-3 w-3" /> {saving ? 'Saving...' : 'Save & Apply'}
          </Button>
        </div>
      </div>

      {hasChanges && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-2 text-sm text-blue-700">
          You have unsaved changes. Click "Save & Apply" to re-classify all transactions.
        </div>
      )}

      <div className="space-y-3">
        <RuleCard
          icon={<Percent className="h-4 w-4 text-amber-600" />}
          title="Discount Threshold"
          description="Flag transactions where discount exceeds this percentage"
          risk="Medium"
          value={config.discount_threshold_percent}
          onChange={v => setConfig(p => ({ ...p, discount_threshold_percent: v }))}
          unit="%"
          step={5}
          hint="Common setting: 10-20% for F&B outlets"
        />

        <RuleCard
          icon={<IndianRupee className="h-4 w-4 text-amber-600" />}
          title="Refund Amount Threshold"
          description="Flag non-cash refunds above this amount (0 = flag all)"
          risk="Medium"
          value={config.refund_amount_threshold}
          onChange={v => setConfig(p => ({ ...p, refund_amount_threshold: v }))}
          unit={'\u20B9'}
          step={100}
          hint="Cash change (return amount) is excluded automatically"
        />

        <RuleCard
          icon={<ShieldAlert className="h-4 w-4 text-amber-600" />}
          title="High Value Transaction"
          description="Flag transactions exceeding this bill amount"
          risk="Medium"
          value={config.high_value_threshold}
          onChange={v => setConfig(p => ({ ...p, high_value_threshold: v }))}
          unit={'\u20B9'}
          step={500}
          hint="Set based on your highest expected normal transaction"
        />

        <RuleCard
          icon={<Package className="h-4 w-4 text-amber-600" />}
          title="Bulk Purchase"
          description="Flag when total item quantity exceeds this count"
          risk="Medium"
          value={config.bulk_quantity_threshold}
          onChange={v => setConfig(p => ({ ...p, bulk_quantity_threshold: v }))}
          unit="items"
          step={1}
        />

        <RuleCard
          icon={<Clock className="h-4 w-4 text-amber-600" />}
          title="POS Idle Alert"
          description="Alert when a POS terminal has no activity for this duration"
          risk="Medium"
          value={config.idle_pos_minutes}
          onChange={v => setConfig(p => ({ ...p, idle_pos_minutes: v }))}
          unit="min"
          step={5}
          hint="Set higher for stores with low traffic"
        />

        {!!config.rules && (
          <Card className="bg-white border-gray-200 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200">
              <h3 className="text-sm font-semibold text-gray-800">Rule Toggles</h3>
              <p className="text-xs text-gray-500 mt-1">Enable or disable individual fraud rules without changing thresholds.</p>
            </div>
            <div className="divide-y divide-gray-100">
              {Object.entries(config.rules).map(([ruleId, rule]) => (
                <div key={ruleId} className="px-4 py-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-medium text-gray-800">{ruleId}</div>
                    <div className="text-xs text-gray-500">{rule.enabled ? 'Enabled' : 'Disabled'}</div>
                  </div>
                  <Switch
                    checked={rule.enabled !== false}
                    onCheckedChange={checked =>
                      setConfig(prev => ({
                        ...prev,
                        rules: {
                          ...(prev.rules || {}),
                          [ruleId]: { ...(prev.rules?.[ruleId] || {}), enabled: checked },
                        },
                      }))
                    }
                  />
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
