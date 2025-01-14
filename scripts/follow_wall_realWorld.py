#!/usr/bin/env python

import math
import random
import sys
import time
import rospy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
from rospy.exceptions import ROSInterruptException

g_distance_wall = 0.2
g_wall_lead = 0.4

g_pub = None
g_sub = None

# Will follow wall from the left: 1
# Will follow wall from the right: -1
g_side = 0

# Angular velocity
g_alpha = 0
# Linear velocity
g_linear_speed = 0.1

# State 0: wandering, looking for wall
# State 1: wall has been found, go in its direction
# State 2: follow wall
g_state = 0

# When the robot turned the last time (used in state 0 and 1)
g_turn_start_time = 0

# Bearing at which the wall was found (used in state 0 and 1)
g_wall_direction = None

# Sim time at which the algorithm started running
g_start_sim_time = None

def update_command_vel(linear_vel, angular_vel):
    msg = Twist()
    msg.linear.x = linear_vel
    msg.angular.z = angular_vel
    g_pub.publish(msg)

# Adaptive code for different lidar sensor ranges
def getDist(msg, dir, region_deg):
    step = len(msg.ranges) // 16
    di_ranges = {
        'N' : msg.ranges[len(msg.ranges) - (region_deg // 2 - 1) : len(msg.ranges) - 1] + msg.ranges[0 : region_deg // 2],
        'NNW' : msg.ranges[step - region_deg // 2 : step + region_deg // 2],
        'NW' : msg.ranges[step*2 - region_deg // 2 : step*2 + region_deg // 2],
        'WNW' : msg.ranges[step*3 - region_deg // 2 : step*3 + region_deg // 2],
        'W' : msg.ranges[step*4 - region_deg // 2 : step*4 + region_deg // 2],
        'E' : msg.ranges[step*12 - region_deg // 2 : step*12 + region_deg // 2],
        'ENE' : msg.ranges[step*13 - region_deg // 2 : step*13 + region_deg // 2],
        'NE' : msg.ranges[step*14 - region_deg // 2 : step*14 + region_deg // 2],
        'NNE' : msg.ranges[step*15 - region_deg // 2 : step*15 + region_deg // 2]
    }

    #ignores the wrong 0.0 values the sensor produces
    return [i if i != 0.0 else math.inf for i in di_ranges[dir]]

def scan_callback(msg):
    scan_max_value = msg.range_max

    # Each region scans 7 degrees
    region_deg = 7
    dirs = ('N', 'NNW', 'NW', 'WNW', 'W', 'E', 'ENE', 'NE', 'NNE')
    regions = {}
    for d in dirs:
        regions[d] = min(min(getDist(msg, d, region_deg)), scan_max_value)

    global g_side, g_alpha, g_state, g_turn_start_time, g_wall_direction

    if g_state == 0:  # wander
        # Check if wall is being detected
        for r, v in regions.items():
            if r in ["N", "W", "E", "NW", "NE"] and v < scan_max_value:
                print('Will change to state 1: drive towards wall ({})'.format(r))
                g_state = 1
                g_wall_direction = r
                g_turn_start_time = time.time()
                return

        # Turn in a random direction every 6 seconds
        delta_time = time.time() - g_turn_start_time

        if delta_time > 6:
            rand = random.randrange(0, 5)

            g_alpha = math.pi / 2 - rand * math.pi / 4

            g_turn_start_time = time.time()
        elif delta_time > 1:
            g_alpha = 0
    elif g_state == 1:  # drive towards wall
        # Minimum distance to start following wall
        min_distance = g_distance_wall + 0.3

        # Check if it is close enough to start following wall
        if regions['N'] < min_distance or regions['NW'] < min_distance or regions['NE'] < min_distance:
            g_state = 2

            if g_side == 0:
                left = (regions['W'] + regions['NW']) / 2
                right = (regions['E'] + regions['NE']) / 2

                if left < scan_max_value or right < scan_max_value:
                    g_side = -1 if right < left else 1
                else:
                    g_side = random.randrange(-1, 2, 2)

            print('Will change to state 2: follow wall from the {}'.format('left' if g_side == 1 else 'right'))
            return

        # Turn to the wall
        delta_time = time.time() - g_turn_start_time

        if delta_time <= 1:
            if g_wall_direction == 'W':
                g_alpha = math.pi / 2
            elif g_wall_direction == 'NW':
                g_alpha = math.pi / 4
            elif g_wall_direction == 'N':
                g_alpha = 0
            elif g_wall_direction == 'NE':
                g_alpha = -math.pi / 4
            elif g_wall_direction == 'E':
                g_alpha = -math.pi / 2
        else:
            g_alpha = 0
    elif g_state == 2:  # follow wall

        y0 = regions['E'] if g_side == -1 else regions['W']
        x1 = (regions['ENE'] if g_side == -1 else regions['WNW']) * math.sin(math.radians(23))
        y1 = (regions['ENE'] if g_side == -1 else regions['WNW']) * math.cos(math.radians(23))

        # If the robot is heading into the wall, turn sideways
        if y0 >= g_distance_wall * 2 and regions['N'] < scan_max_value:
            g_alpha = -math.pi / 4 * g_side
        else:
            # Check scan info from the front of the robot
            front_scan = min([regions['N'], regions['NNW'] + (scan_max_value - regions['WNW']), regions['NNE'] + (scan_max_value - regions['ENE'])])

            # If there is a wall close, adjust turn to not hit it (important for inner corners/turns)
            turn_fix = (0 if front_scan >= 0.5 else 1 - front_scan)

            # Calculate angular velocity to keep wall distance
            abs_alpha = math.atan2(y1 - g_distance_wall,
                                x1 + g_wall_lead - y0) - turn_fix * 1.5
            
            # Choose correct direction for angular velocity
            g_alpha = g_side * abs_alpha

def load_arguments():
    if len(sys.argv) > 1:
        if len(sys.argv) % 2 == 1:
            for i in range(1, len(sys.argv), 2):
                arg = sys.argv[i]
                value = sys.argv[i + 1]

                if arg == '--speed' or arg == '-s':
                    try:
                        value = float(value)
                        global g_linear_speed
                        g_linear_speed = value
                    except ValueError:
                        print('Error parsing speed value')
                        return False
                elif arg == '--wall_distance' or arg == '-d':
                    try:
                        value = float(value)
                        global g_distance_wall
                        g_distance_wall = value
                    except ValueError:
                        print('Error parsing wall distance value')
                        return False
                else:
                    print('Unrecognized argument: ', arg)
                    return False
        else:
            print('Incorrect number of arguments')
            return False

    return True


try:
    if __name__ == '__main__':
        if load_arguments():
            print('Starting with values:')
            print('- linear speed: ', str(g_linear_speed))
            print('- distance to wall: ', str(g_distance_wall))
            print('')

            rospy.init_node('wall_following_robot')

            g_pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
            g_sub = rospy.Subscriber('/scan', LaserScan, scan_callback)

            rate = rospy.Rate(20)

            g_start_sim_time = rospy.get_time()
            print('Started at {} seconds (sim time)'.format(g_start_sim_time))

            while not rospy.is_shutdown():
                update_command_vel(g_linear_speed, g_alpha)
                rate.sleep()
except ROSInterruptException:
    end_sim_time = rospy.get_time()
    print('Finished at {} seconds (sim time)'.format(end_sim_time))
    print('Ran for {} seconds'.format(end_sim_time - g_start_sim_time))
