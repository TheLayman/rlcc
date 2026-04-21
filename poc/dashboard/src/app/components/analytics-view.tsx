import { useMemo } from 'react';
import { Card } from '@/app/components/ui/card';
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, AreaChart, Area, ResponsiveContainer,
} from 'recharts';
import { Transaction } from '@/lib/mock-data';
import { format, subDays, startOfDay, getHours } from 'date-fns';

interface AnalyticsViewProps {
  transactions: Transaction[];
}

const RISK_COLORS = {
  High: '#ef4444',
  Medium: '#f59e0b',
  Low: '#22c55e',
};

const BLUE_PALETTE = ['#3b82f6', '#6366f1', '#06b6d4'];

const TOOLTIP_STYLE = {
  backgroundColor: 'white',
  border: '1px solid #e5e7eb',
  borderRadius: '8px',
  boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)',
};

export function AnalyticsView({ transactions }: AnalyticsViewProps) {
  const riskDistribution = useMemo(() => {
    const counts = { High: 0, Medium: 0, Low: 0 };
    transactions.forEach(t => { counts[t.risk_level]++; });
    return [
      { name: 'High Risk', value: counts.High, color: RISK_COLORS.High },
      { name: 'Medium Risk', value: counts.Medium, color: RISK_COLORS.Medium },
      { name: 'Low Risk', value: counts.Low, color: RISK_COLORS.Low },
    ];
  }, [transactions]);

  const transactionsByDay = useMemo(() => {
    const now = new Date();
    const days: { date: string; total: number; flagged: number }[] = [];

    for (let i = 4; i >= 0; i--) {
      const day = startOfDay(subDays(now, i));
      const nextDay = startOfDay(subDays(now, i - 1));
      const dayTxns = transactions.filter(t =>
        t.timestamp >= day && t.timestamp < nextDay
      );
      const flaggedCount = dayTxns.filter(t => t.risk_level !== 'Low').length;

      days.push({
        date: format(day, 'MMM dd'),
        total: dayTxns.length,
        flagged: flaggedCount,
      });
    }
    return days;
  }, [transactions]);

  const fraudByStore = useMemo(() => {
    const storeMap: Record<string, { name: string; total: number; high: number; medium: number; low: number }> = {};
    transactions.forEach(t => {
      if (!storeMap[t.shop_id]) {
        storeMap[t.shop_id] = { name: t.shop_name || t.shop_id, total: 0, high: 0, medium: 0, low: 0 };
      }
      storeMap[t.shop_id].total++;
      if (t.risk_level === 'High') storeMap[t.shop_id].high++;
      else if (t.risk_level === 'Medium') storeMap[t.shop_id].medium++;
      else storeMap[t.shop_id].low++;
    });
    return Object.entries(storeMap).map(([_, data]) => ({
      store: data.name,
      ...data,
    }));
  }, [transactions]);

  const ruleBreakdown = useMemo(() => {
    const ruleMap: Record<string, number> = {};
    transactions.forEach(t => {
      t.triggered_rules?.forEach(rule => {
        // Strip amounts/details in parentheses to group by rule name
        const groupedName = rule.replace(/\s*\(.*\)$/, '');
        ruleMap[groupedName] = (ruleMap[groupedName] || 0) + 1;
      });
    });
    return Object.entries(ruleMap)
      .map(([rule, count]) => ({ rule, count }))
      .sort((a, b) => b.count - a.count);
  }, [transactions]);

  // --- New chart data ---

  const hourlyActivity = useMemo(() => {
    const hourMap: { hour: number; total: number; flagged: number }[] = Array.from(
      { length: 24 },
      (_, i) => ({ hour: i, total: 0, flagged: 0 })
    );
    transactions.forEach(t => {
      const h = getHours(t.timestamp);
      hourMap[h].total++;
      if (t.risk_level !== 'Low') {
        hourMap[h].flagged++;
      }
    });
    return hourMap;
  }, [transactions]);

  const avgValueByStore = useMemo(() => {
    const storeMap: Record<string, { name: string; sum: number; count: number }> = {};
    transactions.forEach(t => {
      if (!storeMap[t.shop_id]) {
        storeMap[t.shop_id] = { name: t.shop_name || t.shop_id, sum: 0, count: 0 };
      }
      storeMap[t.shop_id].sum += t.transaction_total;
      storeMap[t.shop_id].count++;
    });
    return Object.entries(storeMap)
      .map(([_, data]) => ({
        store: data.name,
        avgValue: Math.round(data.sum / data.count),
      }))
      .sort((a, b) => b.avgValue - a.avgValue);
  }, [transactions]);

  const refundRateTrend = useMemo(() => {
    const now = new Date();
    const days: { date: string; refunds: number }[] = [];

    for (let i = 4; i >= 0; i--) {
      const day = startOfDay(subDays(now, i));
      const nextDay = startOfDay(subDays(now, i - 1));
      const dayTxns = transactions.filter(t =>
        t.timestamp >= day && t.timestamp < nextDay
      );
      const refundCount = dayTxns.filter(t =>
        t.triggered_rules?.some(rule => rule.toLowerCase().includes('refund'))
      ).length;

      days.push({
        date: format(day, 'MMM dd'),
        refunds: refundCount,
      });
    }
    return days;
  }, [transactions]);

  const manualEntryTrend = useMemo(() => {
    const now = new Date();
    const days: { date: string; manualEntry: number }[] = [];

    for (let i = 4; i >= 0; i--) {
      const day = startOfDay(subDays(now, i));
      const nextDay = startOfDay(subDays(now, i - 1));
      const dayTxns = transactions.filter(t => t.timestamp >= day && t.timestamp < nextDay);
      days.push({
        date: format(day, 'MMM dd'),
        manualEntry: dayTxns.filter(t => t.triggered_rules?.some(rule => rule.includes('manual_entry'))).length,
      });
    }
    return days;
  }, [transactions]);

  const voidTrend = useMemo(() => {
    const now = new Date();
    const days: { date: string; voids: number }[] = [];

    for (let i = 4; i >= 0; i--) {
      const day = startOfDay(subDays(now, i));
      const nextDay = startOfDay(subDays(now, i - 1));
      const dayTxns = transactions.filter(t => t.timestamp >= day && t.timestamp < nextDay);
      days.push({
        date: format(day, 'MMM dd'),
        voids: dayTxns.filter(t => t.triggered_rules?.some(rule => rule.includes('void') || rule.includes('cancel'))).length,
      });
    }
    return days;
  }, [transactions]);

  const totalFlagged = transactions.filter(t => t.risk_level !== 'Low').length;
  const flaggedPercent = transactions.length > 0
    ? ((totalFlagged / transactions.length) * 100).toFixed(1)
    : '0';
  const totalValue = transactions.reduce((sum, t) => sum + t.transaction_total, 0);

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <p className="text-sm text-gray-500">Total Transactions</p>
          <p className="text-3xl font-bold text-gray-800">{transactions.length}</p>
          <p className="text-xs text-gray-400 mt-1">Last 5 days</p>
        </Card>
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <p className="text-sm text-gray-500">Flagged Transactions</p>
          <p className="text-3xl font-bold text-red-600">{totalFlagged}</p>
          <p className="text-xs text-gray-400 mt-1">{flaggedPercent}% of total</p>
        </Card>
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <p className="text-sm text-gray-500">Total Value Processed</p>
          <p className="text-3xl font-bold text-gray-800">{'\u20B9'}{totalValue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
          <p className="text-xs text-gray-400 mt-1">Across all stores</p>
        </Card>
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <p className="text-sm text-gray-500">Active Stores</p>
          <p className="text-3xl font-bold text-blue-600">{new Set(transactions.map(t => t.shop_id)).size}</p>
          <p className="text-xs text-gray-400 mt-1">With transactions</p>
        </Card>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Risk Distribution Donut */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Risk Distribution</h3>
          <p className="text-sm text-gray-500 mb-4">Breakdown by risk level</p>
          <div className="flex items-center justify-center">
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={riskDistribution}
                  cx="50%"
                  cy="50%"
                  innerRadius={70}
                  outerRadius={110}
                  paddingAngle={4}
                  dataKey="value"
                  stroke="none"
                >
                  {riskDistribution.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Transactions Over Time */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Transactions Over Time</h3>
          <p className="text-sm text-gray-500 mb-4">Daily volume with flagged overlay</p>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={transactionsByDay}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend />
              <Area
                type="monotone"
                dataKey="total"
                stackId="1"
                stroke="#3b82f6"
                fill="#3b82f6"
                fillOpacity={0.15}
                name="Total"
              />
              <Area
                type="monotone"
                dataKey="flagged"
                stackId="2"
                stroke="#ef4444"
                fill="#ef4444"
                fillOpacity={0.2}
                name="Flagged"
              />
            </AreaChart>
          </ResponsiveContainer>
        </Card>

        {/* Fraud by Store */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Transactions by Store</h3>
          <p className="text-sm text-gray-500 mb-4">Risk breakdown per store</p>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={fraudByStore}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="store" tick={{ fill: '#6b7280', fontSize: 11 }} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend />
              <Bar dataKey="high" stackId="a" fill={RISK_COLORS.High} name="High" radius={[0, 0, 0, 0]} />
              <Bar dataKey="medium" stackId="a" fill={RISK_COLORS.Medium} name="Medium" />
              <Bar dataKey="low" stackId="a" fill={RISK_COLORS.Low} name="Low" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        {/* Rule Breakdown */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Rule Violations</h3>
          <p className="text-sm text-gray-500 mb-4">Most triggered fraud detection rules</p>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={ruleBreakdown} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <YAxis
                dataKey="rule"
                type="category"
                width={180}
                tick={{ fill: '#6b7280', fontSize: 11 }}
              />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Bar
                dataKey="count"
                fill="#3b82f6"
                radius={[0, 4, 4, 0]}
                name="Occurrences"
              />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        {/* Hourly Activity Heatmap */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Hourly Activity</h3>
          <p className="text-sm text-gray-500 mb-4">Transaction count by hour of day with flagged overlay</p>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={hourlyActivity}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="hour"
                tick={{ fill: '#6b7280', fontSize: 11 }}
                tickFormatter={(h: number) => `${h}:00`}
              />
              <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelFormatter={(h: number) => `${h}:00 - ${h}:59`}
              />
              <Legend />
              <Bar dataKey="total" fill="#3b82f6" name="Total" radius={[4, 4, 0, 0]} />
              <Bar dataKey="flagged" fill="#ef4444" name="Flagged" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        {/* Average Transaction Value by Store */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Avg Transaction Value by Store</h3>
          <p className="text-sm text-gray-500 mb-4">Average ticket size per store</p>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={avgValueByStore}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="store" tick={{ fill: '#6b7280', fontSize: 11 }} />
              <YAxis
                tick={{ fill: '#6b7280', fontSize: 12 }}
                tickFormatter={(v: number) => `\u20B9${v.toLocaleString('en-IN')}`}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(value: number) => [`\u20B9${value.toLocaleString('en-IN')}`, 'Avg Value']}
              />
              <Bar dataKey="avgValue" fill="#6366f1" name="Avg Value" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        {/* Refund Rate Trend */}
        <Card className="bg-white border-gray-200 p-6 shadow-sm md:col-span-2">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Refund Rate Trend</h3>
          <p className="text-sm text-gray-500 mb-4">Daily refund-related transaction count over last 5 days</p>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={refundRateTrend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend />
              <Area
                type="monotone"
                dataKey="refunds"
                stroke="#f59e0b"
                fill="#f59e0b"
                fillOpacity={0.2}
                name="Refunds"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </Card>

        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Manual Entry Trend</h3>
          <p className="text-sm text-gray-500 mb-4">Daily transactions tagged with manual entry</p>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={manualEntryTrend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Area type="monotone" dataKey="manualEntry" stroke="#06b6d4" fill="#06b6d4" fillOpacity={0.22} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Card>

        <Card className="bg-white border-gray-200 p-6 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-800 mb-1">Void Trend</h3>
          <p className="text-sm text-gray-500 mb-4">Daily transactions tagged with void or cancellation</p>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={voidTrend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Area type="monotone" dataKey="voids" stroke="#ef4444" fill="#ef4444" fillOpacity={0.22} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      </div>
    </div>
  );
}
