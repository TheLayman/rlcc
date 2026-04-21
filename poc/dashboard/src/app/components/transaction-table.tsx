import { format } from 'date-fns';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/app/components/ui/table';
import { Badge } from '@/app/components/ui/badge';
import { Transaction } from '@/lib/mock-data';

interface TransactionTableProps {
  transactions: Transaction[];
  onRowClick?: (transaction: Transaction) => void;
}

export function TransactionTable({ transactions, onRowClick }: TransactionTableProps) {

  const getRiskBadge = (level: string) => {
    switch (level) {
      case 'High':
        return <Badge className="bg-red-50 text-red-700 border-red-200">High</Badge>;
      case 'Medium':
        return <Badge className="bg-amber-50 text-amber-700 border-amber-200">Medium</Badge>;
      default:
        return <Badge className="bg-green-50 text-green-700 border-green-200">Low</Badge>;
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 overflow-hidden bg-white shadow-sm">
      <Table>
        <TableHeader>
          <TableRow className="bg-gray-50 hover:bg-gray-50 border-gray-200">
            <TableHead className="text-gray-600">Transaction ID</TableHead>
            <TableHead className="text-gray-600">Store</TableHead>
            <TableHead className="text-gray-600">Cashier</TableHead>
            <TableHead className="text-gray-600">Timestamp</TableHead>
            <TableHead className="text-right text-gray-600">Total</TableHead>
            <TableHead className="text-gray-600">Risk</TableHead>
            <TableHead className="text-gray-600">Cam ID</TableHead>
            <TableHead className="text-gray-600">POS ID</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {transactions.map((transaction, index) => (
            <TableRow
              key={transaction.id}
              className={`border-gray-100 hover:bg-blue-50/50 cursor-pointer ${index % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}`}
              onClick={() => onRowClick?.(transaction)}
            >
              <TableCell className="font-mono text-sm text-gray-700">{transaction.id}</TableCell>
              <TableCell className="text-sm text-gray-700">
                <div className="font-medium">{transaction.shop_name || transaction.shop_id}</div>
                <div className="text-xs text-gray-400 font-mono">{transaction.shop_id}</div>
              </TableCell>
              <TableCell className="text-gray-800">{transaction.cashier_name}</TableCell>
              <TableCell className="text-sm text-gray-600">
                {format(transaction.timestamp, 'MMM dd, yyyy HH:mm:ss')}
              </TableCell>
              <TableCell className="text-right font-mono text-gray-800">
                {'\u20B9'}{transaction.transaction_total.toFixed(2)}
              </TableCell>
              <TableCell>{getRiskBadge(transaction.risk_level)}</TableCell>
              <TableCell className="font-mono text-xs text-gray-400">{transaction.cam_id}</TableCell>
              <TableCell className="font-mono text-xs text-gray-400">{transaction.pos_id}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
