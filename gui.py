#!/usr/bin/env python

import sys
import json
import subprocess
import statistics
import random
import threading
import re
from collections import Counter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QVBoxLayout, QLabel, 
    QWidget, QPlainTextEdit, QHBoxLayout, QLineEdit, QPushButton,
    QMessageBox, QSplitter, QTreeWidget, QTreeWidgetItem, QTabWidget,
    QGroupBox, QFormLayout, QScrollArea, QStyledItemDelegate, QStyle,
    QComboBox
)
from PySide6.QtCore import Qt, QMimeData, QRegularExpression, QSize, Signal, QEvent, QRect
from PySide6.QtGui import (
    QKeyEvent, QSyntaxHighlighter, QTextCharFormat, QColor, QFont,
    QIcon, QPainter, QPixmap
)

# Import llm library
import llm

class JsonSyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []
        
        # Define formats for different syntax elements
        self.formats = {
            'string': self.create_format(QColor("#269926")),  # Green for strings
            'number': self.create_format(QColor("#3030F0")),  # Blue for numbers
            'boolean': self.create_format(QColor("#CC6600"), True),  # Orange and bold for true/false
            'null': self.create_format(QColor("#CC6600"), True),     # Orange and bold for null
            'key': self.create_format(QColor("#990000")),     # Red for keys
            'brackets': self.create_format(QColor("#666666"), True), # Gray and bold for brackets
            'colon': self.create_format(QColor("#666666"))    # Gray for colons
        }
        
        # Add highlighting rules using regular expressions
        # String pattern - handles escaped quotes
        self.add_rule(r'"(?:\\.|[^"\\])*"', 'string')
        
        # Numbers pattern
        self.add_rule(r'\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b', 'number')
        
        # Boolean pattern
        self.add_rule(r'\b(?:true|false)\b', 'boolean')
        
        # Null pattern
        self.add_rule(r'\bnull\b', 'null')
        
        # Key pattern (strings followed by colon)
        self.add_rule(r'"(?:\\.|[^"\\])*"(?=\s*:)', 'key')
        
        # Brackets pattern
        self.add_rule(r'[\[\]{}]', 'brackets')
        
        # Colon pattern 
        self.add_rule(r':', 'colon')
    
    def create_format(self, color, bold=False):
        """Create a text format with the specified color and boldness"""
        format = QTextCharFormat()
        format.setForeground(color)
        if bold:
            format.setFontWeight(QFont.Bold)
        return format
    
    def add_rule(self, pattern, format_name):
        """Add a highlighting rule with the given pattern and format"""
        regex = QRegularExpression(pattern)
        self.highlighting_rules.append((regex, self.formats[format_name]))
    
    def highlightBlock(self, text):
        """Apply highlighting to the given block of text"""
        for regex, format in self.highlighting_rules:
            matches = regex.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)

class JsonTextEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighter = JsonSyntaxHighlighter(self.document())
        # Set a monospace font
        font = QFont("Courier New")
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)

class JqQueryInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = None
        self.history = []  # Store query history
        self.history_index = -1  # Current index in history
        self.current_input = ""  # Store current input when browsing history
        
    def set_parent_window(self, parent_window):
        self.parent_window = parent_window
        
    def keyPressEvent(self, event):
        # Handle Up/Down arrow keys for history
        if event.key() == Qt.Key_Up:
            self.navigate_history(1)  # Go back in history
        elif event.key() == Qt.Key_Down:
            self.navigate_history(-1)  # Go forward in history
        # Clear text and reset view when Escape key is pressed
        elif event.key() == Qt.Key_Escape:
            self.clear()
            if self.parent_window and self.parent_window.current_json_file:
                self.parent_window.reset_to_original_json()
        # Run query when Enter/Return is pressed
        elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            query = self.text().strip()
            if query and not self.history or (self.history and query != self.history[-1]):
                self.add_to_history(query)
            self.history_index = -1  # Reset history index
            if self.parent_window:
                self.parent_window.run_jq_query()
        else:
            super().keyPressEvent(event)
    
    def add_to_history(self, query):
        """Add a query to the history"""
        if query and (not self.history or query != self.history[-1]):
            self.history.append(query)
            # Limit history to the last 50 entries
            if len(self.history) > 50:
                self.history = self.history[-50:]
    
    def navigate_history(self, direction):
        """Navigate through history (1 for back, -1 for forward)"""
        if not self.history:
            return
            
        # Save current input if we're just starting to navigate
        if self.history_index == -1:
            self.current_input = self.text()
            
        # Calculate new index
        new_index = self.history_index + direction
        
        # Boundary checks
        if new_index >= len(self.history):
            new_index = len(self.history) - 1  # Clamp to oldest history entry
        elif new_index < -1:
            new_index = -1  # -1 means current input
            
        self.history_index = new_index
        
        # Update text field
        if new_index == -1:
            self.setText(self.current_input)
        else:
            self.setText(self.history[len(self.history) - 1 - new_index])
            
        # Move cursor to end of line
        self.setCursorPosition(len(self.text()))

class JsonSchemaAnalyzer:
    """Analyzes JSON data to extract schema and statistics"""
    
    @staticmethod
    def analyze_json(json_data):
        """Analyze JSON data and return schema information"""
        if isinstance(json_data, dict):
            return JsonSchemaAnalyzer._analyze_object(json_data)
        elif isinstance(json_data, list) and json_data:
            # For arrays, we'll analyze the first few elements 
            # and combine their schemas
            combined_schema = {}
            for i, item in enumerate(json_data[:10]):  # Limit to first 10 items
                if isinstance(item, dict):
                    item_schema = JsonSchemaAnalyzer._analyze_object(item)
                    for key, value in item_schema.items():
                        if key not in combined_schema:
                            combined_schema[key] = value
            
            # Also compute array-level statistics
            array_stats = JsonSchemaAnalyzer._compute_array_stats(json_data)
            combined_schema['__array_stats'] = array_stats
            
            return combined_schema
        else:
            return {}
    
    @staticmethod
    def _analyze_object(obj):
        """Analyze a JSON object (dict)"""
        schema = {}
        for key, value in obj.items():
            value_type = type(value).__name__
            
            if isinstance(value, dict):
                # Recursively analyze nested objects
                schema[key] = {
                    'type': 'object',
                    'properties': JsonSchemaAnalyzer._analyze_object(value)
                }
            elif isinstance(value, list):
                # Analyze array
                array_type = 'array'
                element_type = 'mixed'
                if value and all(isinstance(item, type(value[0])) for item in value):
                    element_type = type(value[0]).__name__
                
                # Calculate array statistics
                array_stats = JsonSchemaAnalyzer._compute_array_stats(value)
                
                schema[key] = {
                    'type': array_type,
                    'element_type': element_type,
                    'stats': array_stats
                }
                
                # If array contains objects, analyze a sample
                if value and isinstance(value[0], dict):
                    # Sample first item as representative
                    sample_schema = JsonSchemaAnalyzer._analyze_object(value[0])
                    schema[key]['properties'] = sample_schema
                    
            else:
                # For primitive values
                stats = JsonSchemaAnalyzer._compute_value_stats(key, value, obj)
                schema[key] = {
                    'type': value_type,
                    'value': value,
                    'stats': stats,
                    'examples': JsonSchemaAnalyzer._get_examples(value)
                }
                
        return schema
    
    @staticmethod
    def _compute_value_stats(key, value, parent_obj=None):
        """Compute statistics for a primitive value"""
        value_type = type(value).__name__
        stats = {'type': value_type}
        
        # Handle different types
        if isinstance(value, (int, float)):
            stats['value'] = value
            # Try to find min/max in sibling objects if parent is a list of objects
            if parent_obj and isinstance(parent_obj, dict):
                # Look for this key in other objects if available
                if hasattr(parent_obj, 'items') and key in parent_obj:
                    values = [parent_obj[key]]
                    stats['min'] = min(values)
                    stats['max'] = max(values)
        elif isinstance(value, str):
            stats['length'] = len(value)
            if len(value) > 50:
                stats['preview'] = value[:50] + "..."
            else:
                stats['preview'] = value
        elif isinstance(value, bool):
            stats['value'] = value
        elif value is None:
            stats['value'] = 'null'
            
        return stats

    @staticmethod
    def _get_examples(value):
        """Get examples of the value, or a random subset for collections"""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return [value]  # Just return the value itself
        elif isinstance(value, list):
            # For lists, return a few random examples if it's big enough
            if len(value) <= 3:
                return value
            else:
                import random
                # Get first, last, and a random middle element
                samples = [value[0], value[len(value)//2], value[-1]]
                return samples
        elif isinstance(value, dict):
            # For dicts, return some key-value pairs
            if len(value) <= 3:
                return list(value.items())
            else:
                import random
                # Get a few random key-value pairs
                keys = list(value.keys())
                samples = [(k, value[k]) for k in keys[:3]]
                return samples
        else:
            return []
    
    @staticmethod
    def _compute_array_stats(arr):
        """Compute statistics for an array"""
        stats = {
            'count': len(arr)
        }
        
        # If the array is empty, return just the count
        if not arr:
            return stats
            
        # Check if all elements are of the same type
        first_type = type(arr[0])
        all_same_type = all(isinstance(item, first_type) for item in arr)
        stats['uniform_type'] = all_same_type
        
        # For numeric arrays, compute statistical measures
        if all_same_type and all(isinstance(item, (int, float)) for item in arr):
            try:
                stats['min'] = min(arr)
                stats['max'] = max(arr)
                stats['mean'] = statistics.mean(arr)
                if len(arr) > 1:
                    stats['median'] = statistics.median(arr)
                    try:
                        stats['stdev'] = statistics.stdev(arr)
                    except:
                        pass  # Ignore if standard deviation calculation fails
                        
                # Get examples (useful for large arrays)
                if len(arr) > 5:
                    stats['examples'] = [arr[0], arr[-1]]  # First and last
                    # Add some values around the median
                    mid_idx = len(arr) // 2
                    if mid_idx > 0 and mid_idx < len(arr):
                        stats['examples'].append(arr[mid_idx])
            except Exception as e:
                pass  # Ignore statistical errors
                
        # For string arrays, find common patterns
        elif all_same_type and all(isinstance(item, str) for item in arr):
            # Get length statistics
            lengths = [len(s) for s in arr]
            stats['min_length'] = min(lengths)
            stats['max_length'] = max(lengths)
            stats['avg_length'] = sum(lengths) / len(lengths)
            
            # Most common values (limited to reasonable array size)
            if len(arr) < 1000:
                counter = Counter(arr)
                most_common = counter.most_common(5)  # Get top 5
                if most_common:
                    stats['most_common'] = [
                        {'value': value, 'count': count} 
                        for value, count in most_common
                    ]
                    
                # Least common values
                if len(counter) > 5:
                    least_common = counter.most_common()[:-6:-1]  # Get bottom 5
                    stats['least_common'] = [
                        {'value': value, 'count': count} 
                        for value, count in least_common
                    ]
                    
            # Sample random examples for large arrays
            if len(arr) > 5:
                import random
                sample_indices = [0, len(arr)-1]  # First and last
                # Add a few random indices
                if len(arr) > 3:
                    sample_indices.extend(random.sample(range(1, len(arr)-1), min(3, len(arr)-2)))
                stats['examples'] = [arr[i] for i in sample_indices]
        
        return stats

class EyeIconDelegate(QStyledItemDelegate):
    """Custom delegate to draw eye icons for visibility toggle"""
    eye_clicked = Signal(QTreeWidgetItem)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.eye_rect_map = {}  # Map items to eye rectangles
        
        # Create icons for visible and hidden states
        self.visible_icon = QStyle.StandardPixmap.SP_DialogYesButton  # Eye icon (green checkmark)
        self.hidden_icon = QStyle.StandardPixmap.SP_DialogNoButton   # Crossed eye icon (red X)
        
    def paint(self, painter, option, index):
        """Paint the eye icon in the tree item"""
        # Paint the default look
        super().paint(painter, option, index)
        
        # Only add eye icon to column 0
        if index.column() == 0:
            tree_item = index.model().data(index, Qt.ItemDataRole.UserRole)
            if tree_item:
                # Get item's hidden state
                is_hidden = tree_item.data(0, Qt.ItemDataRole.UserRole)
                
                # Get the icon based on hidden state
                icon = self.parent().style().standardIcon(
                    self.hidden_icon if is_hidden else self.visible_icon
                )
                
                # Calculate rectangle for eye icon (right side of item)
                rect = option.rect
                icon_size = min(rect.height() - 4, 16)
                x = rect.right() - icon_size - 5
                y = rect.top() + (rect.height() - icon_size) // 2
                
                # Draw the icon
                icon.paint(painter, x, y, icon_size, icon_size)
                
                # Store the icon rectangle for mouse event detection
                self.eye_rect_map[tree_item] = QRect(x, y, icon_size, icon_size)
    
    def editorEvent(self, event, model, option, index):
        """Handle mouse events on the eye icon"""
        if event.type() == QEvent.MouseButtonRelease and index.column() == 0:
            tree_item = model.data(index, Qt.ItemDataRole.UserRole)
            if tree_item and tree_item in self.eye_rect_map:
                # Get the event position
                event_pos = event.pos()
                
                # Check if click was on the eye icon
                if self.eye_rect_map[tree_item].contains(event_pos):
                    # Toggle hidden state
                    current_state = tree_item.data(0, Qt.ItemDataRole.UserRole) or False
                    tree_item.setData(0, Qt.ItemDataRole.UserRole, not current_state)
                    # Emit signal
                    self.eye_clicked.emit(tree_item)
                    return True
        return super().editorEvent(event, model, option, index)

class JsonSchemaTreeWidget(QTreeWidget):
    """Tree widget with custom eye icon handling"""
    visibility_changed = Signal(object, bool)  # Path, is_visible
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Set up delegate for eye icons
        self.delegate = EyeIconDelegate(self)
        self.delegate.eye_clicked.connect(self._handle_eye_clicked)
        self.setItemDelegate(self.delegate)
        
        # Connect clicked signal for item selection
        self.itemClicked.connect(self._handle_item_clicked)
        
        # Track hidden paths
        self.hidden_paths = set()
    
    def _handle_eye_clicked(self, item):
        """Handle eye icon click"""
        path = self._get_item_path(item)
        is_hidden = item.data(0, Qt.ItemDataRole.UserRole) or False
        
        # Update hidden paths set
        if is_hidden:
            self.hidden_paths.add(tuple(path))
        else:
            self.hidden_paths.discard(tuple(path))
            
        # Emit signal for visibility change
        self.visibility_changed.emit(path, not is_hidden)
    
    def _handle_item_clicked(self, item, column):
        """Handle clicks on items (but not on eye icons)"""
        # This will be overridden by the parent class
        pass
    
    def _get_item_path(self, item):
        """Get the path to a tree item"""
        path = []
        current = item
        
        while current:
            text = current.text(0)
            if text != "[Root]" and text != "elements":
                path.insert(0, text)
            current = current.parent()
            
        return path

class JsonSchemaViewer(QWidget):
    """Widget to display JSON schema and statistics"""
    visibility_changed = Signal(object, bool)  # Path, is_visible
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        
        # Tree widget for schema overview with eye icons
        self.schema_tree = JsonSchemaTreeWidget()
        self.schema_tree.setHeaderLabels(["Attribute", "Type"])
        self.schema_tree.setColumnCount(2)
        self.schema_tree._handle_item_clicked = self.show_attribute_details  # Override click handler
        self.schema_tree.visibility_changed.connect(self._handle_visibility_changed)
        layout.addWidget(self.schema_tree)
        
        # Details panel
        self.details_group = QGroupBox("Attribute Details")
        self.details_layout = QFormLayout(self.details_group)
        
        # Scrollable area for details
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.details_group)
        
        layout.addWidget(scroll_area)
        layout.setStretch(0, 2)  # Schema tree gets 2/3
        layout.setStretch(1, 1)  # Details gets 1/3
        
        # Data storage
        self.json_data = None
        self.schema_info = None
    
    def _handle_visibility_changed(self, path, is_visible):
        """Handle visibility change signal from the tree widget"""
        self.visibility_changed.emit(path, is_visible)
    
    def set_json_data(self, json_data):
        """Set the JSON data and analyze its schema"""
        self.json_data = json_data
        self.schema_info = JsonSchemaAnalyzer.analyze_json(json_data)
        self.update_schema_tree()
    
    def update_schema_tree(self):
        """Update the schema tree with the current schema information"""
        self.schema_tree.clear()
        
        if not self.schema_info:
            return
            
        # Handle array root specially
        if '__array_stats' in self.schema_info:
            root_item = QTreeWidgetItem(self.schema_tree, ["[Root]", "array"])
            root_item.setData(0, Qt.ItemDataRole.UserRole, False)  # Not hidden
            # Store the item itself as user data for delegate
            index = self.schema_tree.indexFromItem(root_item, 0)
            self.schema_tree.model().setData(index, root_item, Qt.ItemDataRole.UserRole)
            
            stats = self.schema_info['__array_stats']
            count_item = QTreeWidgetItem(root_item, ["Count", str(stats.get('count', 0))])
            count_item.setData(0, Qt.ItemDataRole.UserRole, False)  # Not hidden
            # Store the item itself as user data for delegate
            index = self.schema_tree.indexFromItem(count_item, 0)
            self.schema_tree.model().setData(index, count_item, Qt.ItemDataRole.UserRole)
            
            # Add rest of array properties
            for key, value in self.schema_info.items():
                if key != '__array_stats':
                    self._add_schema_item(root_item, key, value)
        else:
            # Regular object root
            root_item = QTreeWidgetItem(self.schema_tree, ["[Root]", "object"])
            root_item.setData(0, Qt.ItemDataRole.UserRole, False)  # Not hidden
            # Store the item itself as user data for delegate
            index = self.schema_tree.indexFromItem(root_item, 0)
            self.schema_tree.model().setData(index, root_item, Qt.ItemDataRole.UserRole)
            
            for key, value in self.schema_info.items():
                self._add_schema_item(root_item, key, value)
                
        self.schema_tree.expandToDepth(0)  # Expand first level
    
    def _add_schema_item(self, parent_item, key, schema):
        """Add a schema item to the tree"""
        if isinstance(schema, dict) and 'type' in schema:
            # This is a schema object
            item_type = schema['type']
            tree_item = QTreeWidgetItem(parent_item, [key, item_type])
            tree_item.setData(0, Qt.ItemDataRole.UserRole, False)  # Not hidden
            # Store the item itself as user data for delegate
            index = self.schema_tree.indexFromItem(tree_item, 0)
            self.schema_tree.model().setData(index, tree_item, Qt.ItemDataRole.UserRole)
            
            # Add children for object and array types
            if item_type == 'object' and 'properties' in schema:
                for sub_key, sub_schema in schema['properties'].items():
                    self._add_schema_item(tree_item, sub_key, sub_schema)
            elif item_type == 'array' and 'element_type' in schema:
                element_item = QTreeWidgetItem(tree_item, ["elements", schema['element_type']])
                element_item.setData(0, Qt.ItemDataRole.UserRole, False)  # Not hidden
                # Store the item itself as user data for delegate
                index = self.schema_tree.indexFromItem(element_item, 0)
                self.schema_tree.model().setData(index, element_item, Qt.ItemDataRole.UserRole)
                
                if 'properties' in schema:
                    for sub_key, sub_schema in schema['properties'].items():
                        self._add_schema_item(element_item, sub_key, sub_schema)
        else:
            # This is a simple value or unknown structure
            tree_item = QTreeWidgetItem(parent_item, [key, str(type(schema).__name__)])
            tree_item.setData(0, Qt.ItemDataRole.UserRole, False)  # Not hidden
            # Store the item itself as user data for delegate
            index = self.schema_tree.indexFromItem(tree_item, 0)
            self.schema_tree.model().setData(index, tree_item, Qt.ItemDataRole.UserRole)
    
    def show_attribute_details(self, item, column):
        """Show details for the selected attribute"""
        # Clear previous details
        while self.details_layout.rowCount() > 0:
            self.details_layout.removeRow(0)
        
        # Get the path to this item
        path = self.schema_tree._get_item_path(item)
        if not path:
            return
            
        # Get the schema information for this path
        schema = self._get_schema_at_path(path)
        if not schema:
            return
            
        # Display basic info
        self.details_layout.addRow("Path:", QLabel(" > ".join(path)))
        
        if isinstance(schema, dict):
            if 'type' in schema:
                self.details_layout.addRow("Type:", QLabel(str(schema['type'])))
                
                # Show statistics based on type
                if schema['type'] == 'array' and 'stats' in schema:
                    stats = schema['stats']
                    self.details_layout.addRow("Count:", QLabel(str(stats.get('count', 0))))
                    
                    if 'uniform_type' in stats and stats['uniform_type']:
                        self.details_layout.addRow("Element Type:", 
                                                QLabel(str(schema.get('element_type', 'unknown'))))
                    
                    # Show numeric stats
                    if 'min' in stats:
                        self.details_layout.addRow("Min Value:", QLabel(str(stats['min'])))
                    if 'max' in stats:
                        self.details_layout.addRow("Max Value:", QLabel(str(stats['max'])))
                    if 'mean' in stats:
                        self.details_layout.addRow("Mean:", QLabel(f"{stats['mean']:.2f}"))
                    if 'median' in stats:
                        self.details_layout.addRow("Median:", QLabel(f"{stats['median']:.2f}"))
                    if 'stdev' in stats:
                        self.details_layout.addRow("Std Dev:", QLabel(f"{stats['stdev']:.2f}"))
                    
                    # String stats
                    if 'min_length' in stats:
                        self.details_layout.addRow("Min Length:", QLabel(str(stats['min_length'])))
                    if 'max_length' in stats:
                        self.details_layout.addRow("Max Length:", QLabel(str(stats['max_length'])))
                    if 'avg_length' in stats:
                        self.details_layout.addRow("Avg Length:", 
                                              QLabel(f"{stats['avg_length']:.1f}"))
                    
                    # Examples
                    if 'examples' in stats:
                        self.details_layout.addRow("Examples:", QLabel(""))
                        for i, example in enumerate(stats['examples']):
                            value_str = str(example)
                            if len(value_str) > 50:
                                value_str = value_str[:50] + "..."
                            self.details_layout.addRow(f"  #{i+1}:", QLabel(value_str))
                    
                    # Common values
                    if 'most_common' in stats:
                        self.details_layout.addRow("Most Common:", QLabel(""))
                        for i, common in enumerate(stats['most_common']):
                            value = common['value']
                            if isinstance(value, str) and len(value) > 30:
                                value = value[:30] + "..."
                            label = f"  #{i+1} ({common['count']} times)"
                            self.details_layout.addRow(label, QLabel(str(value)))
                    
                    # Least common
                    if 'least_common' in stats:
                        self.details_layout.addRow("Least Common:", QLabel(""))
                        for i, common in enumerate(stats['least_common']):
                            value = common['value']
                            if isinstance(value, str) and len(value) > 30:
                                value = value[:30] + "..."
                            label = f"  #{i+1} ({common['count']} times)"
                            self.details_layout.addRow(label, QLabel(str(value)))
                
                elif 'stats' in schema:
                    # For primitive types
                    stats = schema['stats']
                    if 'value' in stats:
                        self.details_layout.addRow("Value:", QLabel(str(stats['value'])))
                    if 'min' in stats:
                        self.details_layout.addRow("Min Value:", QLabel(str(stats['min'])))
                    if 'max' in stats:
                        self.details_layout.addRow("Max Value:", QLabel(str(stats['max'])))
                    if 'length' in stats:
                        self.details_layout.addRow("Length:", QLabel(str(stats['length'])))
                    if 'preview' in stats:
                        self.details_layout.addRow("Preview:", QLabel(str(stats['preview'])))
                
                # Show examples
                if 'examples' in schema:
                    self.details_layout.addRow("Examples:", QLabel(""))
                    for i, example in enumerate(schema['examples']):
                        value_str = str(example)
                        if len(value_str) > 50:
                            value_str = value_str[:50] + "..."
                        self.details_layout.addRow(f"  #{i+1}:", QLabel(value_str))
    
    def _get_schema_at_path(self, path):
        """Get the schema information at the given path"""
        if not path:
            return self.schema_info
            
        schema = self.schema_info
        for i, segment in enumerate(path):
            if '__array_stats' in schema and i == 0:
                # Special case for root array
                continue
                
            if isinstance(schema, dict):
                # Regular object property
                if segment in schema:
                    schema = schema[segment]
                # Schema object
                elif 'properties' in schema and segment in schema['properties']:
                    schema = schema['properties'][segment]
                # Array element type
                elif 'type' in schema and schema['type'] == 'array' and 'properties' in schema:
                    # For arrays, the properties are for the elements
                    if segment in schema['properties']:
                        schema = schema['properties'][segment]
                    else:
                        return None
                else:
                    return None
            else:
                return None
                
        return schema

class ChatWidget(QWidget):
    """Widget for chat interface with AI assistant"""
    query_generated = Signal(str)  # Signal for when a jq query is generated
    append_signal = Signal(str)    # Signal for appending text to chat
    update_html_signal = Signal(str)  # Signal for updating HTML
    generate_query_signal = Signal(str)  # Signal for query generation
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
        self._setup_thread_safety()
        self.load_models()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Model selection dropdown
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        model_layout.addWidget(self.model_combo)
        layout.addLayout(model_layout)
        
        # Chat display area
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.append("<i>Welcome to JSON Assistant. Ask questions about your JSON data to generate jq queries.</i>")
        layout.addWidget(self.chat_display)
        
        # Input area
        input_layout = QVBoxLayout()
        self.chat_input = ChatInputTextEdit(self)
        self.chat_input.setPlaceholderText("Ask a question about the JSON data...")
        self.chat_input.setMaximumHeight(80)
        input_layout.addWidget(self.chat_input)
        
        # Send button
        send_button = QPushButton("Ask")
        send_button.clicked.connect(self.send_message)
        input_layout.addWidget(send_button)
        
        layout.addLayout(input_layout)
        
    def _setup_thread_safety(self):
        """Set up signals for thread-safe operations"""
        # Connect signals to slots
        self.append_signal.connect(self.append_to_chat)
        self.update_html_signal.connect(self.update_chat_html)
        self.generate_query_signal.connect(self._emit_query_generated)
    
    def append_to_chat(self, html):
        """Thread-safe method to append text to chat"""
        self.chat_display.append(html)
    
    def update_chat_html(self, html):
        """Thread-safe method to update entire chat HTML"""
        self.chat_display.setHtml(html)
    
    def _emit_query_generated(self, query):
        """Thread-safe method to emit query_generated signal"""
        self.query_generated.emit(query)
        
    def load_models(self):
        """Load available models from llm library"""
        try:
            # First try to detect Ollama models directly
            self._add_ollama_models()
            
            # Get all models from llm
            all_models = list(llm.get_models())
            model_names = []
            
            # Get model names and add them to the combo box (skipping already added Ollama models)
            for model in all_models:
                model_id = model.model_id
                # Skip already added Ollama models
                if not model_id.startswith("ollama:") and self.model_combo.findText(model_id) == -1:
                    model_names.append(model_id)
            
            # Add remaining models to the combo box
            if model_names:
                self.model_combo.addItems(model_names)
                
            # Set default to mistral:latest if available, otherwise first model
            default_model = "ollama:mistral"
            if self.model_combo.findText(default_model) != -1:
                self.model_combo.setCurrentText(default_model)
            elif self.model_combo.count() > 0:
                self.model_combo.setCurrentIndex(0)
                
            self.append_to_chat(f"<i>Using model: {self.model_combo.currentText()}</i>")
            
        except Exception as e:
            self.append_to_chat(f"<i>Error loading models: {str(e)}</i>")
            # Fall back to manually adding models
            if self.model_combo.count() == 0:
                self._add_ollama_models()
                if self.model_combo.count() == 0:
                    self.model_combo.addItem("gpt-3.5-turbo")
                    
    def _add_ollama_models(self):
        """Add Ollama models to the combo box"""
        try:
            # Try to detect if Ollama is installed and add models
            result = subprocess.run(['ollama', 'list'], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[1:]  # Skip header
                added_models = []
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 1:
                            model_name = parts[0]
                            model_id = f"ollama:{model_name}"
                            added_models.append(model_id)
                
                if added_models:
                    self.model_combo.addItems(added_models)
                    self.append_to_chat(f"<i>Found {len(added_models)} Ollama models</i>")
                    return True
            return False
        except Exception as e:
            self.append_to_chat(f"<i>Error detecting Ollama models: {str(e)}</i>")
            return False
    
    def send_message(self):
        """Send user message to the assistant"""
        message = self.chat_input.toPlainText().strip()
        if not message:
            return
            
        # Clear input
        self.chat_input.clear()
        
        # Display user message
        self.append_to_chat(f"<b>You:</b> {message}")
        
        # Get selected model
        model_name = self.model_combo.currentText()
        
        # Add generating message
        generating_id = f"generating_{random.randint(1000, 9999)}"
        self.append_to_chat(f"<span id='{generating_id}'><i>Generating response...</i></span>")
        
        # Start a thread to get response from llm
        threading.Thread(
            target=self.get_response, 
            args=(message, model_name, generating_id),
            daemon=True
        ).start()
    
    def get_response(self, message, model_name, generating_id):
        """Get response from llm in a separate thread"""
        try:
            # Format prompt to ask for jq query
            prompt = f"""You are a helpful assistant specialized in creating jq queries for JSON data.
Based on this question, generate a valid jq filter that would answer it:

Question: "{message}"

Your response should follow this format:
1. First give a brief explanation of what the query will do
2. Then provide only the jq filter command on its own line

Important jq syntax rules and patterns:
- For sorting by date/time fields: '.[] | sort_by(.date_field)'
- For reverse chronological order (newest first): '.[] | sort_by(.date_field) | reverse'
- For chronological order (oldest first): '.[] | sort_by(.date_field)'
- When asked to sort by date but no specific field is mentioned, infer the date field from common names like:
  'date', 'created', 'created_at', 'timestamp', 'modified', 'updated', 'updated_at', etc.
- Sort commands must use correct syntax: '.[] | sort_by(.fieldname)'
- Do NOT use '--key=' format as it's not valid in jq
- For sorting in reverse order: '.[] | sort_by(.fieldname) | reverse'
- For sorting by nested fields: '.[] | sort_by(.parent.child)'

For example:
This will select all elements that have a 'status' field equal to 'active'
.[] | select(.status == "active")

Example for sorting:
This will sort elements by their creation date in descending order (newest first)
.[] | sort_by(.created_at) | reverse

If asked to "sort oldest first" or "sort by oldest date":
.[] | sort_by(.timestamp)  # assuming timestamp is the date field

If asked to "sort newest first" or "sort by date descending":
.[] | sort_by(.created_at) | reverse  # assuming created_at is the date field
"""
            
            # Get response from llm
            response = ""
            try:
                # Decide whether to use llm or direct Ollama call
                if model_name.startswith("ollama:"):
                    # Use direct Ollama call
                    ollama_model = model_name.split(":", 1)[1]
                    result = subprocess.run(
                        ["ollama", "run", ollama_model, prompt],
                        capture_output=True,
                        text=True
                    )
                    response = result.stdout.strip()
                else:
                    # Use llm module
                    model = llm.get_model(model_name)
                    response = model.prompt(prompt)
            except Exception as e:
                raise Exception(f"Error getting response from model: {str(e)}")
            
            # Remove the generating message using HTML
            html = self.chat_display.toHtml()
            generating_span = f"<span id='{generating_id}'><i>Generating response...</i></span>"
            html = html.replace(generating_span, "")
            self.update_html_signal.emit(html)
            
            # Display assistant response
            self.append_signal.emit(f"<b>Assistant:</b> {response}")
            
            # Extract the jq query from the response
            lines = response.split('\n')
            jq_query = None
            for line in lines:
                line = line.strip()
                # Look for a line that looks like a jq query
                if line and not line.startswith(('This', 'Here', 'The', 'I', '#', '*', '-', '1.', '2.')) and ('|' in line or '.' in line):
                    jq_query = line
                    break
            
            if jq_query:
                # Fix common syntax errors in the generated query
                jq_query = self._fix_jq_syntax(jq_query)
                
                # Emit signal with the generated query (thread-safe)
                self.append_signal.emit(f"<i>Generated query: <code>{jq_query}</code></i>")
                
                # Directly emit the signal to avoid the thread-safe mechanism which might be causing issues
                self.query_generated.emit(jq_query)
                
                # Also try the thread-safe method as a backup
                self.generate_query_signal.emit(jq_query)
                
        except Exception as e:
            # Handle errors
            html = self.chat_display.toHtml()
            generating_span = f"<span id='{generating_id}'><i>Generating response...</i></span>"
            html = html.replace(generating_span, "")
            self.update_html_signal.emit(html)
            
            self.append_signal.emit(f"<i>Error: {str(e)}</i>")
            self.append_signal.emit("<i>Try selecting a different model from the dropdown.</i>")
            
    def _fix_jq_syntax(self, query):
        """Fix common syntax errors in jq queries"""
        # Fix incorrect sort syntax
        if '--key=' in query:
            # Replace --key=.field with sort_by(.field)
            match = re.search(r'sort\s+-r\s+--key=(\.\w+[\.\w+]*)', query)
            if match:
                # Descending sort (reverse)
                field = match.group(1)
                query = query.replace(f'sort -r --key={field}', f'sort_by({field}) | reverse')
            else:
                # Ascending sort
                match = re.search(r'sort\s+--key=(\.\w+[\.\w+]*)', query)
                if match:
                    field = match.group(1)
                    query = query.replace(f'sort --key={field}', f'sort_by({field})')
        
        # Fix sort -r (without --key=)
        if re.search(r'sort\s+-r\b', query):
            # Check if there's a field specified
            match = re.search(r'sort\s+-r\s+(\.\w+[\.\w+]*)', query)
            if match:
                field = match.group(1)
                query = query.replace(f'sort -r {field}', f'sort_by({field}) | reverse')
            else:
                # Generic sort -r
                query = query.replace('sort -r', 'sort | reverse')
                    
        # Other common error fixes could be added here
        
        return query

class ChatInputTextEdit(QPlainTextEdit):
    """Custom text edit for chat that handles Enter key to send messages"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.chat_widget = parent
        
    def keyPressEvent(self, event):
        # Send message when Enter is pressed without Shift
        if event.key() == Qt.Key_Return and not event.modifiers() & Qt.ShiftModifier:
            self.chat_widget.send_message()
        else:
            super().keyPressEvent(event)

class JsonViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-Assisted JSON Viewer")
        self.setGeometry(100, 100, 1200, 700)  # Wider window to accommodate both sidebars
        self.current_json_file = None
        self.json_content = None
        self.parsed_json = None
        self.hidden_paths = set()  # Track hidden attributes
        
        # Create main widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Add jq query input and run button
        query_layout = QHBoxLayout()
        self.query_input = JqQueryInput()
        self.query_input.set_parent_window(self)
        self.query_input.setPlaceholderText("Enter jq query (e.g., '.[]', '.name', etc.)")
        run_button = QPushButton("Run Query")
        run_button.clicked.connect(self.run_jq_query)
        query_layout.addWidget(QLabel("jq:"))
        query_layout.addWidget(self.query_input)
        query_layout.addWidget(run_button)
        main_layout.addLayout(query_layout)
        
        # Add instructions label
        self.instruction_label = QLabel("Drag and drop a JSON file here")
        self.instruction_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.instruction_label)
        
        # Create main horizontal splitter for chat, content and schema
        main_splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(main_splitter)
        
        # Add chat sidebar (left side)
        self.chat_widget = ChatWidget()
        
        # Connect all possible signals from the chat widget to the query handler
        self.chat_widget.query_generated.connect(self.set_query_from_chat)
        self.chat_widget.generate_query_signal.connect(self.set_query_from_chat)
        
        main_splitter.addWidget(self.chat_widget)
        
        # Create secondary splitter for content and schema
        content_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(content_splitter)
        
        # Add JSON content viewer with syntax highlighting (middle)
        self.json_viewer = JsonTextEdit()
        self.json_viewer.setReadOnly(True)
        content_splitter.addWidget(self.json_viewer)
        
        # Add schema viewer (right side)
        self.schema_viewer = JsonSchemaViewer()
        self.schema_viewer.visibility_changed.connect(self.toggle_attribute_visibility)
        content_splitter.addWidget(self.schema_viewer)
        
        # Set initial splitter sizes
        main_splitter.setSizes([250, 950])  # Left chat, right content+schema
        content_splitter.setSizes([650, 300])  # Content vs schema
        
        # Enable drag and drop
        self.setAcceptDrops(True)
    
    def set_query_from_chat(self, query):
        """Set jq query from chat-generated query and run it"""
        self.query_input.setText(query)
        self.run_jq_query()
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].toLocalFile().endswith('.json'):
            event.acceptProposedAction()
    
    def dropEvent(self, event):
        file_path = event.mimeData().urls()[0].toLocalFile()
        self.load_json_file(file_path)
    
    def toggle_attribute_visibility(self, path, is_visible):
        """Handle visibility toggle for attributes"""
        path_tuple = tuple(path)
        
        if is_visible:
            self.hidden_paths.discard(path_tuple)
        else:
            self.hidden_paths.add(path_tuple)
        
        # Update the JSON view with filtered content
        if self.parsed_json:
            filtered_json = self.filter_json_by_visibility(self.parsed_json)
            formatted_json = json.dumps(filtered_json, indent=4)
            self.json_viewer.setPlainText(formatted_json)
    
    def filter_json_by_visibility(self, json_data):
        """Filter the JSON data based on hidden paths"""
        if not self.hidden_paths:
            return json_data  # No filtering needed
        
        def _filter_recursive(data, current_path=()):
            """Recursively filter data based on path"""
            if isinstance(data, dict):
                result = {}
                for key, value in data.items():
                    # Create the path for this key
                    key_path = current_path + (key,)
                    
                    # Skip if this exact path is hidden
                    if key_path in self.hidden_paths:
                        continue
                    
                    # Process the value recursively
                    filtered_value = _filter_recursive(value, key_path)
                    result[key] = filtered_value
                    
                return result
            elif isinstance(data, list):
                # For lists, apply filtering to each item
                return [_filter_recursive(item, current_path) for item in data]
            else:
                # Primitive types are returned as is
                return data
        
        return _filter_recursive(json_data)
    
    def load_json_file(self, file_path):
        try:
            with open(file_path, 'r') as f:
                self.json_content = f.read()
                
            # Store current file path
            self.current_json_file = file_path
            
            # Reset hidden paths
            self.hidden_paths = set()
                
            # Try to parse and format JSON
            try:
                self.parsed_json = json.loads(self.json_content)
                formatted_json = json.dumps(self.parsed_json, indent=4)
                self.json_viewer.setPlainText(formatted_json)
                self.instruction_label.setText(f"Loaded: {file_path}")
                
                # Update schema viewer
                self.schema_viewer.set_json_data(self.parsed_json)
                
            except json.JSONDecodeError as e:
                self.json_viewer.setPlainText(f"Invalid JSON: {str(e)}\n\n{self.json_content}")
                self.instruction_label.setText(f"Error loading JSON from: {file_path}")
                self.current_json_file = None
                self.json_content = None
                self.parsed_json = None
                
        except Exception as e:
            self.json_viewer.setPlainText(f"Error opening file: {str(e)}")
            self.instruction_label.setText("Error loading file")
            self.current_json_file = None
            self.json_content = None
            self.parsed_json = None
    
    def reset_to_original_json(self):
        """Reset the view to show the original JSON content"""
        if self.json_content:
            try:
                self.parsed_json = json.loads(self.json_content)
                # Apply visibility filters
                filtered_json = self.filter_json_by_visibility(self.parsed_json)
                formatted_json = json.dumps(filtered_json, indent=4)
                self.json_viewer.setPlainText(formatted_json)
                self.instruction_label.setText(f"Loaded: {self.current_json_file}")
            except json.JSONDecodeError:
                self.json_viewer.setPlainText(self.json_content)
    
    def run_jq_query(self):
        if not self.current_json_file:
            QMessageBox.warning(self, "No File Loaded", "Please drag and drop a JSON file first.")
            return
        
        query = self.query_input.text().strip()
        if not query:
            # If query is empty, reset to original JSON
            self.reset_to_original_json()
            return
        
        try:
            # Run jq command using subprocess
            cmd = ["jq", query, self.current_json_file]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                # Try to format the output as JSON
                try:
                    output_json = json.loads(result.stdout)
                    # Apply visibility filters
                    filtered_json = self.filter_json_by_visibility(output_json)
                    formatted_output = json.dumps(filtered_json, indent=4)
                    self.json_viewer.setPlainText(formatted_output)
                    
                    # Update schema viewer with filtered data
                    self.schema_viewer.set_json_data(output_json)
                    
                except json.JSONDecodeError:
                    # If it's not valid JSON, just show the raw output
                    self.json_viewer.setPlainText(result.stdout)
                
                self.instruction_label.setText(f"Query executed: {query}")
            else:
                self.json_viewer.setPlainText(f"jq error: {result.stderr}")
                self.instruction_label.setText(f"Query failed: {query}")
        
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to run jq query: {str(e)}")
            self.instruction_label.setText(f"Error running query: {query}")


# Create the application instance
app = QApplication(sys.argv)

# Create and show the main window
window = JsonViewerWindow()
window.show()

# Start the event loop
app.exec()
